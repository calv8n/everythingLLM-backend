import asyncio
import logging
import re
import time

from ...config import settings
from .agents import OrchestratorAgent, classify_follow_up
from .agent_sandbox import AgentSandbox
from .code_validation import ValidationAgent
from .docker_service import DockerExecutionService
from .event_bus import EventBus
from .input_guard import requires_safety_review, validate_prompt
from .llm_service import PROMPT_VERSION
from .output_controls import clean_output, extract_result, gate_artifacts, requested_deliverables
from .session_memory import append_message, get_messages
from .artifact_store import save_task_files

logger = logging.getLogger(__name__)

_REFUSAL_PATTERN = re.compile(r"^\s*REFUSED:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def _file_links(task_id: str | None, files: list[dict]) -> list[dict]:
    """Add REST download/preview URLs to file payloads so the frontend can
    fetch artifacts independently of the (one-time) WebSocket result event."""
    if not task_id:
        return files
    linked = []
    for entry in files:
        name = entry.get("name", "")
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name) or "file"
        item = dict(entry)
        item["url"] = f"/api/task/{task_id}/files/{safe_name}"
        linked.append(item)
    return linked


async def prepare_task_prompt(
    prompt: str,
    bus: EventBus,
    *,
    groq_api_key: str | None = None,
    groq_model: str | None = None,
    sandbox: AgentSandbox,
    task_id: str | None = None,
    web_search: bool = False,
    search_provider: str | None = None,
    allowed_outputs: list[str] | None = None,
) -> str:
    """Run planning/research agents and prepare implementation instructions."""
    started_at = time.monotonic()
    logger.info("Planning/research started task_id=%s", task_id or "unknown")
    try:
        implementation_prompt = await OrchestratorAgent(sandbox).prepare_implementation_prompt(
            prompt,
            bus,
            groq_api_key=groq_api_key,
            groq_model=groq_model,
            web_search=web_search,
            search_provider=search_provider,
            allowed_outputs=allowed_outputs,
        )
    except Exception:
        logger.exception("Planning/research failed task_id=%s", task_id or "unknown")
        raise
    logger.info(
        "Planning/research completed task_id=%s duration_seconds=%.2f prompt_chars=%d",
        task_id or "unknown",
        time.monotonic() - started_at,
        len(implementation_prompt),
    )
    return implementation_prompt


async def implement_task(
    prompt: str,
    bus: EventBus,
    *,
    groq_api_key: str | None = None,
    groq_model: str | None = None,
    sandbox: AgentSandbox,
    task_id: str | None = None,
    allowed_outputs: list[str] | None = None,
) -> tuple[str, str, list[str]]:
    """Run the implementation agent and return its response, code, requirements."""
    started_at = time.monotonic()
    logger.info("Implementation generation started task_id=%s prompt_chars=%d", task_id or "unknown", len(prompt))
    try:
        response, code, requirements = await OrchestratorAgent(sandbox).implement(
            prompt,
            bus,
            groq_api_key=groq_api_key,
            groq_model=groq_model,
            allowed_outputs=allowed_outputs,
        )
    except Exception:
        logger.exception("Implementation generation failed task_id=%s", task_id or "unknown")
        raise
    logger.info(
        "Implementation generation completed task_id=%s duration_seconds=%.2f response_chars=%d code_chars=%d requirement_count=%d",
        task_id or "unknown",
        time.monotonic() - started_at,
        len(response),
        len(code),
        len(requirements),
    )
    return response, code, requirements


async def execute_task(
    code: str,
    requirements: list[str],
    sandbox: AgentSandbox,
) -> dict:
    """Run generated code through the implementation agent's sandbox tool."""
    return await OrchestratorAgent(sandbox).execute(code, requirements)


def _build_repair_prompt(
    original_prompt: str,
    failed_code: str,
    failure_output: str,
    attempt: int,
    max_attempts: int,
    previous_messages: list[dict[str, str]] | None = None,
) -> str:
    max_log_chars = 16_000
    if len(failure_output) > max_log_chars:
        failure_output = (
            "[Earlier output omitted; showing the final part of the execution log.]\n"
            + failure_output[-max_log_chars:]
        )

    context_limit = max(0, settings.SESSION_MEMORY_MESSAGES)
    context_messages = (previous_messages or [])[-context_limit:] if context_limit else []
    if context_messages:
        context_lines = []
        for message in context_messages:
            content = message.get("content", "")
            if len(content) > 4_000:
                content = content[:4_000] + "\n[Message truncated to preserve context space.]"
            context_lines.append(f"{message.get('role', 'unknown').capitalize()}: {content}")
        prior_context = "\n\n".join(context_lines)
    else:
        prior_context = "[No prior session messages were retained.]"

    return f"""The previous generated script failed. This is repair attempt {attempt} of {max_attempts}.

Previous session messages (most recent six retained messages):
{prior_context}

Original user request:
{original_prompt}

Failed generated Python code:
```python
{failed_code or "[No Python code block was generated.]"}
```

Execution error and logs:
```text
{failure_output or "[No execution output was produced.]"}
```

Diagnose the failure using the code and logs, then generate a corrected, complete replacement script that fulfills the original request. Validate response shapes and check for empty data before indexing. Keep the required output format: brief analysis/plan followed by exactly one complete Python code block with its # REQUIREMENTS line. Do not just describe the fix; provide runnable replacement code."""


def _build_session_prompt(prompt: str, previous_messages: list[dict[str, str]]) -> str:
    if not previous_messages:
        return prompt

    transcript = "\n\n".join(
        f"{message['role'].capitalize()}: {message['content']}"
        for message in previous_messages
    )
    return f"""Recent conversation context (most recent messages):
{transcript}

New user request:
{prompt}

Apply the new request to the previous task/result when relevant. Generate a complete replacement Python script that incorporates the requested changes, rather than only describing what changed."""


def _assistant_memory_content(
    code: str,
    result_text: str,
    file_names: list[str] | None = None,
) -> str:
    max_code_chars = 12_000
    max_result_chars = 4_000
    saved_code = code[:max_code_chars]
    if len(code) > max_code_chars:
        saved_code += "\n[Code truncated in session memory.]"
    saved_result = result_text[:max_result_chars]
    if len(result_text) > max_result_chars:
        saved_result += "\n[Output truncated in session memory.]"
    files = ", ".join(file_names or []) or "none"
    return (
        f"Generated code:\n```python\n{saved_code}\n```\n"
        f"Execution output:\n{saved_result}\nOutput files: {files}"
    )


async def run_task(
    prompt: str,
    bus: EventBus,
    *,
    groq_api_key: str | None = None,
    groq_model: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    web_search: bool = False,
    search_provider: str | None = None,
):
    """Main pipeline to generate a plan and code based on the user prompt."""
    task_started_at = time.monotonic()
    docker_service: DockerExecutionService | None = None
    try:
        logger.info(
            "Task pipeline started task_id=%s session_id=%s prompt_chars=%d prompt_version=%s",
            task_id or "unknown",
            session_id or "none",
            len(prompt),
            PROMPT_VERSION,
        )
        if validate_prompt(prompt) == "block":
            await bus.publish({
                "type": "error",
                "content": "The request exceeds the prompt limit or matches a disallowed activity.",
            })
            return
        allowed_outputs = requested_deliverables(prompt)
        safety_review = requires_safety_review(prompt)
        # Initialize Docker before asking any agent to plan or research. This makes
        # a missing daemon/image fail immediately instead of after generation.
        docker_service = DockerExecutionService(task_id=task_id)
        logger.info("Docker initialization started task_id=%s image=%s", task_id or "unknown", docker_service.image)
        docker_started_at = time.monotonic()
        await docker_service.initialize()
        logger.info(
            "Docker initialization completed task_id=%s image=%s duration_seconds=%.2f",
            task_id or "unknown",
            docker_service.image,
            time.monotonic() - docker_started_at,
        )
        sandbox = AgentSandbox(docker_service, bus)

        previous_messages: list[dict[str, str]] = []
        if session_id:
            context_limit = max(0, settings.SESSION_MEMORY_MESSAGES)
            if context_limit:
                previous_messages = get_messages(session_id)[-context_limit:]
            if previous_messages:
                try:
                    is_follow_up = await classify_follow_up(
                        prompt,
                        previous_messages,
                        bus,
                        groq_api_key=groq_api_key,
                        groq_model=groq_model,
                    )
                except ValueError:
                    logger.warning(
                        "Follow-up classification failed; treating request as independent task_id=%s",
                        task_id or "unknown",
                        exc_info=True,
                    )
                    is_follow_up = False
                if not is_follow_up:
                    logger.info(
                        "Prior session context omitted for independent request task_id=%s session_id=%s",
                        task_id or "unknown",
                        session_id,
                    )
                    previous_messages = []
            append_message(session_id, "user", prompt)

        generation_prompt = _build_session_prompt(prompt, previous_messages)
        if safety_review:
            generation_prompt = (
                "SAFETY REVIEW REQUIRED: Assess the request against the safety rules before "
                "planning. Do not perform suspicious scanning, access private or metadata "
                "addresses, reveal environment or credential data, or enable harmful activity. "
                "If disallowed, return REFUSED with a short reason.\n\n"
                + generation_prompt
            )
        prepare_prompt_kwargs = {
            "groq_api_key": groq_api_key,
            "groq_model": groq_model,
            "sandbox": sandbox,
            "task_id": task_id,
            "allowed_outputs": allowed_outputs,
        }
        if web_search or search_provider:
            prepare_prompt_kwargs["web_search"] = web_search
            prepare_prompt_kwargs["search_provider"] = search_provider
        implementation_prompt = await prepare_task_prompt(
            generation_prompt,
            bus,
            **prepare_prompt_kwargs,
        )
        max_retries = max(0, settings.CODE_RETRY_ATTEMPTS)
        max_attempts = max_retries + 1
        current_prompt = implementation_prompt
        code = ""
        failure_output = ""
        failure_code = ""
        failure_exit_code: int | str = "unknown"
        validation_agent = ValidationAgent()
        repeated_validation_errors: dict[str, int] = {}

        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                logger.warning(
                    "Repair retry started task_id=%s attempt=%d max_retries=%d previous_status=%s failure_log_chars=%d",
                    task_id or "unknown",
                    attempt - 1,
                    max_retries,
                    failure_exit_code,
                    len(failure_output),
                )
                await bus.publish({
                    "type": "retry",
                    "content": (
                        f"Attempt {attempt - 1} failed. Sending the code and execution "
                        f"logs to the model for repair (retry {attempt - 1}/{max_retries})."
                    ),
                })
                current_prompt = _build_repair_prompt(
                    prompt,
                    failure_code,
                    failure_output,
                    attempt - 1,
                    max_retries,
                    previous_messages,
                )

            full_response, code, requirements = await implement_task(
                current_prompt,
                bus,
                groq_api_key=groq_api_key,
                groq_model=groq_model,
                sandbox=sandbox,
                task_id=task_id,
                allowed_outputs=allowed_outputs,
            )

            refusal = _REFUSAL_PATTERN.search(full_response)
            if refusal and "```" not in full_response:
                refusal_message = clean_output(refusal.group(1).strip())
                await bus.publish({
                    "type": "error",
                    "content": f"Request declined by safety policy: {refusal_message}",
                })
                if session_id:
                    append_message(session_id, "assistant", f"Request declined: {refusal_message}")
                return

            if not code:
                failure_code = ""
                failure_exit_code = "no code generated"
                failure_output = (
                    "The model did not return a fenced Python code block. "
                    f"Its response was:\n{full_response}"
                )
                failure_output = clean_output(failure_output)
                logger.warning(
                    "Implementation produced no code task_id=%s attempt=%d response_chars=%d",
                    task_id or "unknown",
                    attempt,
                    len(full_response),
                )
            else:
                failure_code = code
                validation = validation_agent.validate(
                    code,
                    requirements,
                    require_headers=True,
                    allowed_outputs=allowed_outputs,
                )
                if not validation.valid:
                    failure_exit_code = "validation error"
                    failure_output = clean_output(
                        validation.error or "Generated code failed validation."
                    )
                    repeated_validation_errors[failure_output] = (
                        repeated_validation_errors.get(failure_output, 0) + 1
                    )
                    logger.warning(
                        "Generated code validation failed task_id=%s attempt=%d error=%s",
                        task_id or "unknown",
                        attempt,
                        failure_output,
                    )
                    if repeated_validation_errors[failure_output] >= 2:
                        await bus.publish({
                            "type": "error",
                            "content": clean_output(
                                "Generated code repeated the same validation failure; "
                                f"execution was stopped: {failure_output}"
                            ),
                        })
                        break
                    if attempt < max_attempts:
                        continue
                    await bus.publish({
                        "type": "error",
                        "content": clean_output(
                            f"Generated code failed validation after {max_attempts} attempt(s): "
                            f"{failure_output}"
                        ),
                    })
                    break

                requirements = validation.requirements
                logger.info(
                    "Generated code validated task_id=%s attempt=%d discovered_imports=%d pip_packages=%d",
                    task_id or "unknown",
                    attempt,
                    len(validation.discovered_imports),
                    len(requirements),
                )
                execution_started_at = time.monotonic()
                logger.info(
                    "Code execution started task_id=%s attempt=%d code_chars=%d requirement_count=%d",
                    task_id or "unknown",
                    attempt,
                    len(code),
                    len(requirements),
                )
                await bus.publish({"type": "executing", "content": "Running generated code"})
                try:
                    result = await execute_task(code, requirements, sandbox)
                except asyncio.TimeoutError:
                    failure_exit_code = "timeout"
                    failure_output = (
                        f"Execution timed out after {settings.EXECUTION_TIMEOUT_SECONDS} seconds."
                    )
                    logger.warning(
                        "Code execution timed out task_id=%s attempt=%d timeout_seconds=%d",
                        task_id or "unknown",
                        attempt,
                        settings.EXECUTION_TIMEOUT_SECONDS,
                    )
                except Exception as execution_error:
                    failure_exit_code = type(execution_error).__name__
                    failure_output = clean_output(
                        f"Execution raised {failure_exit_code}: {execution_error}"
                    )
                    logger.exception(
                        "Code execution raised an exception; preparing repair task_id=%s attempt=%d error_type=%s",
                        task_id or "unknown",
                        attempt,
                        failure_exit_code,
                    )
                else:
                    failure_exit_code = result.get("exit_code", "unknown")
                    logger.info(
                        "Code execution completed task_id=%s attempt=%d exit_code=%s duration_seconds=%.2f output_chars=%d output_file_count=%d",
                        task_id or "unknown",
                        attempt,
                        failure_exit_code,
                        time.monotonic() - execution_started_at,
                        len(result.get("stdout") or ""),
                        len(result.get("files", [])),
                    )
                    if failure_exit_code == 0:
                        execution_output = clean_output(result.get("stdout") or "")
                        result_files = result.get("files", [])
                        output_validation_error = validation_agent.validate_execution_output(
                            execution_output
                        )
                        if output_validation_error:
                            failure_exit_code = "output validation error"
                            failure_output = (
                                f"{output_validation_error}\n"
                                f"Complete execution output:\n{execution_output or '[empty]'}"
                            )
                            logger.warning(
                                "Execution output validation failed; scheduling code repair task_id=%s attempt=%d",
                                task_id or "unknown",
                                attempt,
                            )
                        else:
                            allowed_files, dropped_files = gate_artifacts(
                                result_files,
                                allowed_outputs,
                            )
                            if dropped_files:
                                await bus.publish({
                                    "type": "log",
                                    "content": clean_output(
                                        "Skipped unrequested or oversized artifacts: "
                                        + ", ".join(dropped_files)
                                    ),
                                })
                            # Persist artifacts on the host and add REST URLs
                            # so the frontend can download/preview files even
                            # after the sandbox container is removed.
                            if task_id and allowed_files:
                                save_task_files(task_id, allowed_files)
                            downloadable_files = _file_links(task_id, allowed_files)
                            result_text = clean_output(extract_result(execution_output))
                            await bus.publish({
                                "type": "result",
                                "content": result_text,
                                "files": downloadable_files,
                            })
                            if session_id:
                                append_message(
                                    session_id,
                                    "assistant",
                                    _assistant_memory_content(
                                        code,
                                        result_text,
                                        [file.get("name", "file") for file in result_files],
                                    ),
                                )
                            logger.info(
                                "Task completed task_id=%s session_id=%s attempt=%d total_duration_seconds=%.2f",
                                task_id or "unknown",
                                session_id or "none",
                                attempt,
                                time.monotonic() - task_started_at,
                            )
                            return
                    else:
                        failure_output = result.get("stdout") or "No execution output was produced."

            if attempt < max_attempts:
                continue

            await bus.publish(
                {
                    "type": "error",
                    "content": clean_output(
                        f"Code execution failed after {max_attempts} attempt(s) "
                        f"(last status: {failure_exit_code}). Output: {failure_output}"
                    ),
                }
            )
            logger.error(
                "Task exhausted retries task_id=%s attempts=%d final_status=%s failure_log_chars=%d",
                task_id or "unknown",
                max_attempts,
                failure_exit_code,
                len(failure_output),
            )
            if session_id:
                append_message(
                    session_id,
                    "assistant",
                    _assistant_memory_content(
                        failure_code,
                        f"Execution failed after {max_attempts} attempt(s): {failure_output}",
                    ),
                )

    except ConnectionError:
        logger.exception("Task failed due to connection error task_id=%s session_id=%s", task_id or "unknown", session_id or "none")
        if session_id:
            append_message(session_id, "assistant", "The task failed due to a connection error.")
        await bus.publish({
            "type": "error",
            "content": "Connection error occurred. Please check your network and try again."
        })

    except Exception as e:
        logger.exception("Task failed unexpectedly task_id=%s session_id=%s", task_id or "unknown", session_id or "none")
        if session_id:
            append_message(
                session_id,
                "assistant",
                clean_output(f"The task failed: {e}"),
            )
        await bus.publish({
            "type": "error",
            "content": clean_output(f"An unexpected error occurred: {str(e)}")
        })

    finally:
        if docker_service is not None:
            try:
                await docker_service.close()
                logger.info("Docker client closed task_id=%s session_id=%s", task_id or "unknown", session_id or "none")
            except Exception:
                logger.exception("Failed to close Docker client task_id=%s session_id=%s", task_id or "unknown", session_id or "none")
        logger.info(
            "Task pipeline finished task_id=%s session_id=%s duration_seconds=%.2f",
            task_id or "unknown",
            session_id or "none",
            time.monotonic() - task_started_at,
        )
        await bus.finish()
