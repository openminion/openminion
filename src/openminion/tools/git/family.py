"""Git tool family declaration."""

from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

from .plugin import (
    GitAddArgs,
    GitBlameArgs,
    GitBranchArgs,
    GitCheckoutArgs,
    GitCommitArgs,
    GitDiffArgs,
    GitLogArgs,
    GitReflogArgs,
    GitResetArgs,
    GitShowArgs,
    GitStashArgs,
    GitStatusArgs,
    _h_add,
    _h_blame,
    _h_branch,
    _h_checkout,
    _h_commit,
    _h_diff,
    _h_log,
    _h_reflog,
    _h_reset,
    _h_show,
    _h_stash,
    _h_status,
)

GIT_FAMILY = ToolFamilySpec(
    module_id="git",
    # Default for the bulk of the family; READ_ONLY tools override and
    # `git.reset` overrides to POWER_USER.
    min_scope_default="WRITE_SAFE",
    common_tags=("plugin", "git"),
    common_capabilities=("git",),
    tools=(
        # ---- READ_ONLY surface (NGT-01) -----------------------------------
        ToolDecl(
            name="git.status",
            args_model=GitStatusArgs,
            handler=_h_status,
            description="Show the working tree status (branch, ahead/behind, files).",
            min_scope="READ_ONLY",
            idempotent=True,
            capabilities=("read_only",),
        ),
        ToolDecl(
            name="git.diff",
            args_model=GitDiffArgs,
            handler=_h_diff,
            description="Show changes between commits, the index, or the working tree.",
            min_scope="READ_ONLY",
            idempotent=True,
            capabilities=("read_only",),
        ),
        ToolDecl(
            name="git.log",
            args_model=GitLogArgs,
            handler=_h_log,
            description="Show the commit history with structured commit objects.",
            min_scope="READ_ONLY",
            idempotent=True,
            capabilities=("read_only",),
        ),
        ToolDecl(
            name="git.show",
            args_model=GitShowArgs,
            handler=_h_show,
            description="Show commit details and diff for a given ref.",
            min_scope="READ_ONLY",
            idempotent=True,
            capabilities=("read_only",),
        ),
        ToolDecl(
            name="git.blame",
            args_model=GitBlameArgs,
            handler=_h_blame,
            description="Show line-by-line attribution for a file.",
            min_scope="READ_ONLY",
            idempotent=True,
            capabilities=("read_only",),
        ),
        # ---- WRITE_SAFE surface (NGT-02 + NGT-03) -------------------------
        ToolDecl(
            name="git.branch",
            args_model=GitBranchArgs,
            handler=_h_branch,
            description=(
                "List, create, or delete branches. Action is explicit; "
                "force-delete requires explicit approval."
            ),
            idempotent=False,
            capabilities=("write_safe",),
            block_under_readonly=True,
        ),
        ToolDecl(
            name="git.checkout",
            args_model=GitCheckoutArgs,
            handler=_h_checkout,
            description=(
                "Switch to a branch, tag, or commit. Refuses on a "
                "dirty working tree; flags detached-HEAD state."
            ),
            idempotent=False,
            capabilities=("write_safe",),
            block_under_readonly=True,
        ),
        ToolDecl(
            name="git.add",
            args_model=GitAddArgs,
            handler=_h_add,
            description=(
                "Stage one or more explicit paths to the index. "
                "No implicit 'add everything'."
            ),
            idempotent=True,
            capabilities=("write_safe",),
            block_under_readonly=True,
        ),
        ToolDecl(
            name="git.commit",
            args_model=GitCommitArgs,
            handler=_h_commit,
            description=(
                "Commit the staged changes with a required message. "
                "Never uses --no-verify or --amend."
            ),
            idempotent=False,
            capabilities=("write_safe",),
            block_under_readonly=True,
        ),
        ToolDecl(
            name="git.stash",
            args_model=GitStashArgs,
            handler=_h_stash,
            description=(
                "Manage the stash. Action discriminator: 'push', "
                "'list', 'apply', 'pop' (apply + drop), 'drop' "
                "(destructive — requires confirm), 'clear' "
                "(destructive — requires confirm)."
            ),
            idempotent=False,
            capabilities=("write_safe",),
            block_under_readonly=True,
        ),
        # ---- POWER_USER surface (NGT-04) ----------------------------------
        ToolDecl(
            name="git.reset",
            args_model=GitResetArgs,
            handler=_h_reset,
            description=(
                "Reset HEAD to a ref. Modes: mixed (default), soft, "
                "hard (destructive — requires confirm)."
            ),
            min_scope="POWER_USER",
            dangerous=True,
            idempotent=False,
            capabilities=("power_user",),
            block_under_readonly=True,
        ),
        ToolDecl(
            name="git.reflog",
            args_model=GitReflogArgs,
            handler=_h_reflog,
            description="Show the reflog entries.",
            min_scope="READ_ONLY",
            idempotent=True,
            capabilities=("read_only",),
        ),
    ),
)


__all__ = ["GIT_FAMILY"]
