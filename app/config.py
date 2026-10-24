from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 数据与检查点
    database_url: str = "postgresql+asyncpg://whole_home:whole_home@localhost:5433/whole_home"
    checkpoint_pg_dsn: str = "postgresql://whole_home:whole_home@localhost:5433/whole_home"

    # LLM 网关
    llm_base_url: str = "http://10.10.12.248:8081"
    llm_auth_token: str = ""
    llm_model: str = "strata"
    llm_timeout_s: float = 120.0
    llm_plan_max_retries: int = 1
    llm_max_tokens: int = 2000

    # 设备适配器:mock | home_assistant(§6.12)
    device_adapter: str = "mock"
    ha_base_url: str = ""
    ha_token: str = ""

    # 上下文
    context_char_budget: int = 6000
    context_recent_turns: int = 6

    # 首版单用户默认身份(接入认证后由请求鉴权提供)
    default_user_id: str = "u1"
    default_home_id: str = "h1"
    default_timezone: str = "Asia/Shanghai"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
