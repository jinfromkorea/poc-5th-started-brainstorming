"""Generates a Maven settings.xml that mirrors ALL repositories to a single
public URL (default: Maven Central) -- for running this tool outside the
corporate network. A target project's own pom.xml may declare an internal
Nexus (e.g. a group-wide mirror) as an additional <repository>; that
declaration doesn't mean every artifact resolved through it is actually
internal-only -- confirmed empirically that a plain public 3rd-party
artifact (com.azure:azure-json, part of the Azure SDK) failed outright with
an unresolvable-host error when that internal Nexus wasn't reachable, even
though the artifact itself is public. Maven doesn't automatically fall back
to another repository on a network-level failure the way it does on a plain
404, so the fix is a <mirror> that redirects resolution to a reachable
public repository instead.

Opt-in via Settings.mvn_public_mirror_enabled -- this changes dependency
resolution for the whole build, so it shouldn't silently apply when the
internal Nexus IS reachable (e.g. on the corporate network/VPN, where it may
serve security-reviewed/curated artifact versions that differ from the
public ones).
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings

_SETTINGS_XML_TEMPLATE = """<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">
  <mirrors>
    <mirror>
      <id>public-mirror-fallback</id>
      <mirrorOf>*</mirrorOf>
      <url>{url}</url>
    </mirror>
  </mirrors>
</settings>
"""


def public_mirror_settings_path(settings: Settings) -> Path:
    """Writes (if not already present) a settings.xml redirecting every
    Maven repository to settings.mvn_public_mirror_url, and returns its
    path. One shared file for the whole process (not per-job) since its
    content only depends on config, not on any job's own data. Rewritten
    only if missing -- Settings itself is cached for the process lifetime
    (app.config.get_settings), so the URL can't change without a restart
    anyway."""
    path = settings.jobs_dir / "_mvn-public-mirror-settings.xml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_SETTINGS_XML_TEMPLATE.format(url=settings.mvn_public_mirror_url), encoding="utf-8")
    return path


def with_public_mirror(args: list[str], settings: Settings) -> list[str]:
    """If ``args`` is a Maven invocation and the public-mirror fallback is
    enabled, inserts ``-s <settings.xml>`` right after the executable name.
    A no-op otherwise (including for non-mvn commands like `trivy`/`git`)."""
    if not args or args[0] != "mvn" or not settings.mvn_public_mirror_enabled:
        return args
    settings_path = public_mirror_settings_path(settings)
    return [args[0], "-s", str(settings_path), *args[1:]]
