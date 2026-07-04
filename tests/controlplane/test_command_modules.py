from openminion.base.version import OPENMINION_VERSION
from openminion.modules.controlplane.commands.registry import CommandRegistry
from openminion.modules.controlplane.commands.module import (
    CommandSpec,
    CommandSchema,
    AuthRequirement,
)
from openminion.modules.controlplane.contracts.models import (
    CommandResult,
    ParsedCommand,
    ResolvedContext,
)
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore


def dummy_handler_success(command, ctx):
    return CommandResult(ok=True, text="Success", data={"test": True})


def dummy_handler_failure(command, ctx):
    return CommandResult(ok=False, text="Failure", error={"reason": "test"})


def test_command_spec_creation():
    schema = CommandSchema(
        name="test.command", description="A test command", usage="/test.command [args]"
    )

    spec = CommandSpec(
        name="test.command",
        schema=schema,
        handler=dummy_handler_success,
        auth_requirement=AuthRequirement.USER,
        module_name="test_module",
    )

    assert spec.name == "test.command"
    assert spec.auth_requirement == AuthRequirement.USER
    assert spec.module_name == "test_module"
    assert spec.version == OPENMINION_VERSION


def test_command_registry_creation():
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)

    assert len(registry.list_commands()) > 0
    assert "help" in registry.list_commands()

    help_spec = registry.get_command_spec("help")
    assert help_spec is not None
    assert help_spec.name == "help"
    assert help_spec.module_name == "builtin"


def test_register_command_spec_basic():
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)

    schema = CommandSchema(
        name="test.custom",
        description="A custom test command",
        usage="/test.custom <arg>",
    )

    spec = CommandSpec(
        name="test.custom",
        schema=schema,
        handler=dummy_handler_success,
        auth_requirement=AuthRequirement.USER,
        module_name="test_module",
    )

    registry.register_command_spec(spec)

    assert "test.custom" in registry.list_commands()
    retrieved_spec = registry.get_command_spec("test.custom")
    assert retrieved_spec is not None
    assert retrieved_spec.module_name == "test_module"


def test_command_spec_collision_handling():
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)

    schema = CommandSchema(
        name="help",
        description="Custom help (should be shadowed)",
        usage="/help custom",
    )

    spec = CommandSpec(
        name="help",
        schema=schema,
        handler=dummy_handler_failure,
        auth_requirement=AuthRequirement.NONE,
        module_name="shadow_test_module",
    )

    success = registry.register_command_spec(spec)
    assert success is False

    help_spec = registry.get_command_spec("help")
    assert help_spec.module_name == "builtin"

    assert "help" in registry.list_shadowed_commands()
    shadowed_spec = registry.shadowed_commands["help"]
    assert shadowed_spec.module_name == "shadow_test_module"


def test_auth_requirement_tracking():
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)

    schema = CommandSchema(
        name="test.admin", description="Admin-only test command", usage="/test.admin"
    )

    admin_spec = CommandSpec(
        name="test.admin",
        schema=schema,
        handler=dummy_handler_success,
        auth_requirement=AuthRequirement.ADMIN,
        module_name="admin_module",
    )

    registry.register_command_spec(admin_spec)

    auth_req = registry.get_command_auth_requirement("test.admin")
    assert auth_req == AuthRequirement.ADMIN

    user_spec = CommandSpec(
        name="test.user",
        schema=CommandSchema(
            name="test.user", description="User test command", usage="/test.user"
        ),
        handler=dummy_handler_success,
        auth_requirement=AuthRequirement.USER,
        module_name="user_module",
    )

    registry.register_command_spec(user_spec)
    user_auth_req = registry.get_command_auth_requirement("test.user")
    assert user_auth_req == AuthRequirement.USER


def test_execute_with_commandspec():
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)

    ctx = ResolvedContext(
        session_id="test_session",
        user_key="test_user",
        chat_key="test_chat",
        agent_id="test_agent",
        role="user",
        trace_id="test_trace",
        span_id="test_span",
    )
    parsed_cmd = ParsedCommand(canonical="help", original_text="/help", args=[])

    result = registry.execute(parsed_cmd, ctx)
    assert result.ok is True
    assert "Available commands:" in result.text


def test_module_diagnostics():
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)

    custom_spec = CommandSpec(
        name="diagnostic.test",
        schema=CommandSchema(
            name="diagnostic.test",
            description="Diagnostic test command",
            usage="/diagnostic.test",
        ),
        handler=dummy_handler_success,
        auth_requirement=AuthRequirement.USER,
        module_name="test_diag_module",
    )

    registry.register_command_spec(custom_spec)

    diag_info = registry.list_modules()

    assert "builtin" in diag_info["built_in"]
    assert "test_diag_module" in diag_info["loaded"]
    assert len(diag_info["loaded"]) >= 1  # Has our test module


def test_module_diagnostics_command():
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)

    custom_spec = CommandSpec(
        name="diagnostics.test",
        schema=CommandSchema(
            name="diagnostics.test",
            description="Test command for diagnostics",
            usage="/diagnostics.test",
        ),
        handler=dummy_handler_success,
        auth_requirement=AuthRequirement.USER,
        module_name="diagnostics_test_module",
        version=OPENMINION_VERSION,
    )
    registry.register_command_spec(custom_spec)

    ctx = ResolvedContext(
        session_id="test_session",
        user_key="test_user",
        chat_key="test_chat",
        agent_id="test_agent",
        role="user",
        trace_id="test_trace",
        span_id="test_span",
    )
    parsed_cmd = ParsedCommand(canonical="modules", original_text="/modules", args=[])

    result = registry.execute(parsed_cmd, ctx)

    assert result.ok is True
    assert "Module Diagnostics:" in result.text
    assert "Built-in (" in result.text
    assert result.data is not None
    assert "builtin" in result.data.get("built_in", [])
    assert "diagnostics_test_module" in result.data.get("loaded", [])


def test_broken_module_tracking():
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)

    dummy_exception = ValueError("Test exception for broken module")
    registry.register_broken_module("broken_test_module", dummy_exception)

    broken_modules = registry.get_broken_modules()
    assert "broken_test_module" in broken_modules
    assert broken_modules["broken_test_module"].error_type == "ValueError"

    assert registry.is_broken_module("broken_test_module") is True
    assert registry.is_broken_module("nonexistent_module") is False


def test_shadowed_command_tracking():
    store = InMemoryControlPlaneStore()
    registry = CommandRegistry(store=store)

    # Create original spec
    original_spec = CommandSpec(
        name="shadow.test",
        schema=CommandSchema(
            name="shadow.test", description="Original command", usage="/shadow.test"
        ),
        handler=dummy_handler_success,
        auth_requirement=AuthRequirement.USER,
        module_name="original_module",
    )
    registry.register_command_spec(original_spec)

    # Create shadowing spec with same name but different module
    shadowing_spec = CommandSpec(
        name="shadow.test",  # Same name to cause shadow
        schema=CommandSchema(
            name="shadow.test",
            description="Shadowing command",
            usage="/shadow.test new",
        ),
        handler=dummy_handler_failure,
        auth_requirement=AuthRequirement.ADMIN,
        module_name="shadowing_module",
    )

    # This should shadow the original because original wins
    shadow_result = registry.register_command_spec(shadowing_spec)
    assert shadow_result is False  # Failed to register (shadowed)

    # Check both exist - original registered, shadow in shadowed
    original_retrieved = registry.get_command_spec("shadow.test")
    assert original_retrieved.module_name == "original_module"

    assert "shadow.test" in registry.list_shadowed_commands()
    shadowed = registry.shadowed_commands["shadow.test"]
    assert shadowed.module_name == "shadowing_module"
