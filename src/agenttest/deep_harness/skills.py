"""Package-owned instructions exposed through Deep Agents' virtual filesystem."""

from importlib.resources import files

SKILL_ROOT = "/skills"
SKILL_NAMES = (
    "api-functional-testing",
    "api-regression-testing",
    "api-failure-investigation",
)


def skill_path(name: str) -> str:
    return f"{SKILL_ROOT}/{name}/SKILL.md"


def skill_files() -> dict:
    from deepagents.backends.utils import create_file_data

    directory = files("agenttest.deep_harness").joinpath("skills")
    return {
        skill_path(name): create_file_data(directory.joinpath(name, "SKILL.md").read_text(
            encoding="utf-8"))
        for name in SKILL_NAMES
    }


def skill_filesystem(backend, paths):
    """Expose only the native reader, with a deny rule after the exact allowed paths."""
    from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission

    permissions = [FilesystemPermission(operations=["read"], paths=list(paths))] if paths else []
    permissions.append(FilesystemPermission(operations=["read", "write"], paths=["/**"],
                                            mode="deny"))
    return FilesystemMiddleware(
        backend=backend, tools=["read_file"], permissions=permissions,
        custom_tool_descriptions={"read_file": "读取 Skills 列表中指定的 SKILL.md 虚拟路径。"},
        system_prompt="read_file 只读取随 AgentTest 发布的指南。用领域工具读取授权和业务证据。",
    )
