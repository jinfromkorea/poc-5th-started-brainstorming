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


def build_tools(work_dir: Path, settings: Settings) -> list[BaseTool]:
    @tool
    async def read_file(relative_path: str) -> str:
        """Read a file's contents. relative_path is relative to the project root."""
        return _safe_path(work_dir, relative_path).read_text(encoding="utf-8")

    @tool
    async def edit_file(relative_path: str, content: str) -> str:
        """Overwrite a file with new content (creating parent directories if
        needed). relative_path is relative to the project root. Provide the
        FULL new file content, not a diff."""
        target = _safe_path(work_dir, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {relative_path}"

    @tool
    async def run_build() -> str:
        """Run `mvn compile` against the project and return exit code + combined output."""
        result = await mvn_compile(work_dir, settings)
        return f"exit={result.returncode}\n{result.output}"

    @tool
    async def run_recipe(recipe: str, artifact: str) -> str:
        """Apply one OpenRewrite recipe. `recipe` is the fully-qualified
        recipe class name (e.g. org.openrewrite.java.migrate.UpgradeToJava21).
        `artifact` is the Maven coordinates of the artifact it lives in
        (e.g. org.openrewrite.recipe:rewrite-migrate-java:RELEASE)."""
        result = await run_openrewrite_recipes(work_dir, [recipe], [artifact], settings)
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
