"""LangChain tools bound to a specific job's work_dir/settings, given to the
AI-fix agent (spec: "LLM에는 runOpenRewriteRecipe, runBuild, ... readFile/
editFile 같은 함수 호출(tool calling)을 제공"). Built fresh per job via
build_tools() rather than as module-level tools, since each job has its own
work_dir the AI must be sandboxed to.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool, tool

from app.config import Settings
from app.mvnrewrite.mvn_client import mvn_compile
from app.mvnrewrite.recipe_catalog import RecipeCatalog
from app.mvnrewrite.rewrite_client import run_openrewrite_recipes
from app.mvnrewrite.subprocess_runner import build_log_path


class ToolPathEscapeError(Exception):
    pass


def _safe_path(work_dir: Path, relative_path: str) -> Path:
    """Same escape-prevention principle as the ZIP ingest guard, applied
    here to the AI's file tools -- edit_file is powerful enough that it must
    be sandboxed to work_dir, exactly like everything else the AI touches."""
    target = (work_dir / relative_path).resolve()
    work_resolved = work_dir.resolve()
    if work_resolved not in target.parents and target != work_resolved:
        raise ToolPathEscapeError(f"path escapes project working copy: {relative_path!r}")
    return target


def _existing_ancestor(path: Path, floor: Path) -> Path:
    """Walk up from ``path`` to the nearest directory that actually exists,
    never going above ``floor`` (work_dir). Used to point a hallucinated
    path back at what's really there."""
    probe = path
    while not probe.exists() and probe != floor:
        probe = probe.parent
    return probe


def build_tools(work_dir: Path, settings: Settings, output_dir: Path, stage: str) -> list[BaseTool]:
    @tool
    async def read_file(relative_path: str) -> str:
        """Read a file's contents. relative_path is relative to the project
        root. Returns an "Error: ..." string (does not raise) if the path
        doesn't exist, is a directory, or isn't valid UTF-8 text -- so a
        guessed/hallucinated path becomes a normal tool result the agent can
        see and correct, instead of crashing the whole job."""
        try:
            target = _safe_path(work_dir, relative_path)
        except ToolPathEscapeError as exc:
            return f"Error: {exc}"

        # Checked up front (not caught as IsADirectoryError/FileNotFoundError
        # around read_text) because opening a directory raises PermissionError
        # on Windows, not IsADirectoryError like on POSIX -- confirmed
        # empirically. Checking .is_dir()/.exists() first is portable.
        if target.is_dir():
            entries = sorted(p.name for p in target.iterdir())
            return f"Error: {relative_path} is a directory, not a file. Contents: {', '.join(entries)}"
        if not target.exists():
            ancestor = _existing_ancestor(target.parent, work_dir)
            entries = sorted(p.name for p in ancestor.iterdir()) if ancestor.is_dir() else []
            hint = f" Real entries under {ancestor.relative_to(work_dir)}: {', '.join(entries)}" if entries else ""
            return f"Error: file not found: {relative_path}.{hint}"

        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: {relative_path} exists but is not valid UTF-8 text (likely a binary file) -- cannot read it with this tool."
        except OSError as exc:
            return f"Error: could not read {relative_path}: {exc}"

    @tool
    async def edit_file(relative_path: str, content: str) -> str:
        """Overwrite a file with new content (creating parent directories if
        needed). relative_path is relative to the project root. Provide the
        FULL new file content, not a diff. Returns an "Error: ..." string
        (does not raise) if the path escapes the project or can't be
        written -- so a bad path becomes a normal tool result the agent can
        see and correct, instead of crashing the whole job."""
        try:
            target = _safe_path(work_dir, relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"wrote {len(content)} chars to {relative_path}"
        except ToolPathEscapeError as exc:
            return f"Error: {exc}"
        except OSError as exc:
            return f"Error: could not write {relative_path}: {exc}"

    @tool
    async def run_build() -> str:
        """Run `mvn compile` against the project and return exit code + combined output."""
        log_path = build_log_path(output_dir, stage, "ai-fix-build")
        result = await mvn_compile(work_dir, settings, log_path=log_path)
        return f"exit={result.returncode}\n{result.output}"

    @tool
    async def run_recipe(recipe: str, artifact: str) -> str:
        """Apply one OpenRewrite recipe. `recipe` is the fully-qualified
        recipe class name (e.g. org.openrewrite.java.migrate.UpgradeToJava21).
        `artifact` is the Maven coordinates of the artifact it lives in
        (e.g. org.openrewrite.recipe:rewrite-migrate-java:RELEASE)."""
        log_path = build_log_path(output_dir, stage, "ai-fix-recipe")
        result = await run_openrewrite_recipes(work_dir, [recipe], [artifact], settings, log_path=log_path)
        return f"exit={result.returncode}\n{result.output}"

    @tool
    def list_available_recipes() -> str:
        """List the known OpenRewrite migration steps (Spring Boot / Java /
        Spring AI) this tool has cataloged, each with a confidence level.
        confidence=unverified means the recipe name is a best guess by
        naming convention, not confirmed to actually exist -- try it, and if
        it fails, fall back to editing files directly."""
        catalog = RecipeCatalog.load()
        lines: list[str] = []
        for step in catalog.spring_boot_steps:
            lines.append(
                f"spring-boot {step.from_version}->{step.to_version}: "
                f"{step.recipe or '(no known recipe -- edit directly)'} [{step.confidence}]"
            )
        for step in catalog.java_steps:
            lines.append(f"java ->{step.to_version}: {step.recipe or '(no known recipe)'} [{step.confidence}]")
        for step in catalog.spring_ai_steps:
            lines.append(
                f"spring-ai ->{step.to_version}: {step.recipe or '(no known recipe -- edit directly)'} [{step.confidence}]"
            )
        return "\n".join(lines)

    return [read_file, edit_file, run_build, run_recipe, list_available_recipes]
