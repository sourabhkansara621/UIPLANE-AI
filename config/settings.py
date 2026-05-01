"""
config/settings.py
------------------
Central configuration loaded from environment variables / .env file.
All other modules import from here - never read os.environ directly.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    # -- Application --------------------------------------------HTML 
    app_name: str = Field("UNIPLANE AI Platform", alias="APP_NAME")
    app_env: str = Field("development", alias="APP_ENV")
    debug: bool = Field(True, alias="DEBUG")

    # -- JWT ----------------------------------------------------
    jwt_secret_key: str = Field("change-me", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field("HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(480, alias="JWT_EXPIRE_MINUTES")

    # -- Database -----------------------------------------------
    database_url: str = Field(
        "postgresql://k8sai:password@localhost:5432/k8sai_db",
        alias="DATABASE_URL",
    )

    # -- Redis --------------------------------------------------
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")

    # -- AI -----------------------------------------------------
    anthropic_api_key: str = Field("", alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(
        "claude-sonnet-4-20250514", alias="ANTHROPIC_MODEL"
    )
    # Supported values: auto | anthropic | github_models | copilot
    ai_provider: str = Field("auto", alias="AI_PROVIDER")
    # Supported values: default | anthropic | github_models | copilot
    general_chat_provider: str = Field("default", alias="GENERAL_CHAT_PROVIDER")
    github_models_token: str = Field("", alias="GITHUB_MODELS_TOKEN")
    github_models_endpoint: str = Field(
        "https://models.inference.ai.azure.com/chat/completions",
        alias="GITHUB_MODELS_ENDPOINT",
    )
    github_models_model: str = Field(
        "openai/gpt-4.1-mini",
        alias="GITHUB_MODELS_MODEL",
    )

    # -- Kubernetes ---------------------------------------------
    k8s_kubeconfig_paths: str = Field("", alias="K8S_KUBECONFIG_PATHS")
    k8s_use_in_cluster: bool = Field(False, alias="K8S_USE_IN_CLUSTER")
    mcp_enabled: bool = Field(False, alias="MCP_ENABLED")
    mcp_cluster_endpoints: str = Field("", alias="MCP_CLUSTER_ENDPOINTS")
    mcp_timeout_seconds: int = Field(10, alias="MCP_TIMEOUT_SECONDS")
    mcp_gke_kubeconfig_paths: str = Field("", alias="MCP_GKE_KUBECONFIG_PATHS")
    mcp_eks_kubeconfig_paths: str = Field("", alias="MCP_EKS_KUBECONFIG_PATHS")
    mcp_aks_kubeconfig_paths: str = Field("", alias="MCP_AKS_KUBECONFIG_PATHS")
    mcp_datadog_targets: str = Field("", alias="MCP_DATADOG_TARGETS")

    # -- Vault --------------------------------------------------
    vault_url: str = Field("http://localhost:8200", alias="VAULT_URL")
    vault_token: str = Field("root", alias="VAULT_TOKEN")

    # -- Datadog ------------------------------------------------
    datadog_api_key: str = Field("", alias="DATADOG_API_KEY")
    datadog_app_key: str = Field("", alias="DATADOG_APP_KEY")
    datadog_site: str = Field("datadoghq.com", alias="DATADOG_SITE")

    # -- Safety guards ------------------------------------------
    prod_mutation_require_approval: bool = Field(
        True, alias="PROD_MUTATION_REQUIRE_APPROVAL"
    )
    max_memory_limit_gb: int = Field(10, alias="MAX_MEMORY_LIMIT_GB")

    model_config = {"env_file": ".env", "populate_by_name": True}


@lru_cache
def get_settings() -> Settings:
    """Return cached singleton settings object."""
    return Settings()
