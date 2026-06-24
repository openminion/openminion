"""Fixed internal vocabulary for the knowledge-graph layer."""

# Layer names. A provider registers into exactly one layer.
LAYER_SECOND_BRAIN = "second_brain"
LAYER_THIRD_BRAIN = "provider"

KNOWLEDGE_GRAPH_LAYERS: frozenset[str] = frozenset(
    {
        LAYER_SECOND_BRAIN,
        LAYER_THIRD_BRAIN,
    }
)

# Provider tags. Tags are descriptive labels inside a layer, never peer roles.
TAG_DOCUMENT_GRAPH = "document_graph"
TAG_CODE_GRAPH = "code_graph"
TAG_ARTIFACT_GRAPH = "artifact_graph"
TAG_HOSTED_GRAPH = "hosted_graph"
TAG_HYBRID_GRAPH = "hybrid_graph"

KNOWLEDGE_GRAPH_PROVIDER_TAGS: frozenset[str] = frozenset(
    {
        TAG_DOCUMENT_GRAPH,
        TAG_CODE_GRAPH,
        TAG_ARTIFACT_GRAPH,
        TAG_HOSTED_GRAPH,
        TAG_HYBRID_GRAPH,
    }
)

# Capability names. Runtime routes by capability rather than provider name.
CAPABILITY_QUERY = "query"
CAPABILITY_PATH = "path"
CAPABILITY_NEIGHBORHOOD = "neighborhood"
CAPABILITY_EXPLAIN = "explain"
CAPABILITY_REFRESH = "refresh"
CAPABILITY_WATCH = "watch"
CAPABILITY_CITATIONS = "citations"
CAPABILITY_PROVENANCE = "provenance"
CAPABILITY_WRITABLE_GRAPH = "writable_graph"
CAPABILITY_DURABLE_MEMORY = "durable_memory"
CAPABILITY_PROMOTE_CANDIDATES = "promote_candidates"
CAPABILITY_PROMOTES_TO_DURABLE = "promotes_to_durable"

KNOWLEDGE_GRAPH_CAPABILITIES: frozenset[str] = frozenset(
    {
        CAPABILITY_QUERY,
        CAPABILITY_PATH,
        CAPABILITY_NEIGHBORHOOD,
        CAPABILITY_EXPLAIN,
        CAPABILITY_REFRESH,
        CAPABILITY_WATCH,
        CAPABILITY_CITATIONS,
        CAPABILITY_PROVENANCE,
        CAPABILITY_WRITABLE_GRAPH,
        CAPABILITY_DURABLE_MEMORY,
        CAPABILITY_PROMOTE_CANDIDATES,
        CAPABILITY_PROMOTES_TO_DURABLE,
    }
)

# Telemetry event names emitted by the knowledge-graph layer.
EVENT_SOURCE_RESOLVED = "knowledge_graph.source.resolved"
EVENT_QUERY_STARTED = "knowledge_graph.query.started"
EVENT_QUERY_COMPLETED = "knowledge_graph.query.completed"
EVENT_QUERY_DEGRADED = "knowledge_graph.query.degraded"
EVENT_QUERY_FAILED = "knowledge_graph.query.failed"
EVENT_REFRESH_STARTED = "knowledge_graph.refresh.started"
EVENT_REFRESH_COMPLETED = "knowledge_graph.refresh.completed"
EVENT_REFRESH_FAILED = "knowledge_graph.refresh.failed"

KNOWLEDGE_GRAPH_TELEMETRY_EVENTS: frozenset[str] = frozenset(
    {
        EVENT_SOURCE_RESOLVED,
        EVENT_QUERY_STARTED,
        EVENT_QUERY_COMPLETED,
        EVENT_QUERY_DEGRADED,
        EVENT_QUERY_FAILED,
        EVENT_REFRESH_STARTED,
        EVENT_REFRESH_COMPLETED,
        EVENT_REFRESH_FAILED,
    }
)

# Top-level config key under OpenMinionConfig.module_configs.
KNOWLEDGE_GRAPHS_CONFIG_KEY = "knowledge_graphs"

# First supported third-brain provider adapter.
PROVIDER_GRAPHIFY = "graphify"
PROVIDER_PRAGMAGRAPH = "pragmagraph"

# Provider option keys consumed by the Graphify adapter.
GRAPHIFY_OPTION_CAPABILITIES = "capabilities"
GRAPHIFY_OPTION_COMMAND = "command"
GRAPHIFY_OPTION_COMMAND_ARGS = "command_args"
GRAPHIFY_OPTION_GRAPH_ID = "graph_id"
GRAPHIFY_OPTION_GRAPH_PATH = "graph_path"
GRAPHIFY_OPTION_TIMEOUT_SECONDS = "timeout_seconds"

# Provider option keys consumed by the PragmaGraph adapter.
PRAGMAGRAPH_OPTION_CAPABILITIES = "capabilities"
PRAGMAGRAPH_OPTION_COMMAND = "command"
PRAGMAGRAPH_OPTION_COMMAND_ARGS = "command_args"
PRAGMAGRAPH_OPTION_GRAPH_ID = "graph_id"
PRAGMAGRAPH_OPTION_NAMESPACE = "namespace"
PRAGMAGRAPH_OPTION_ROOT_PATH = "root_path"
PRAGMAGRAPH_OPTION_SNAPSHOT_PATH = "snapshot_path"
PRAGMAGRAPH_OPTION_TIMEOUT_SECONDS = "timeout_seconds"
