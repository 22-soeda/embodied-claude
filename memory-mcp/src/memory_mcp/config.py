"""Configuration for Memory MCP Server."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class MemoryConfig:
    """Memory storage configuration."""

    db_path: str
    collection_name: str
    # Embedding model options (changing model requires re-embedding existing memories):
    #   "intfloat/multilingual-e5-base"  — 768-dim, ~1.1GB, higher quality (default)
    #   "intfloat/multilingual-e5-small" — 384-dim, ~471MB, lighter (good for low-resource envs)
    embedding_model: str = "intfloat/multilingual-e5-base"
    enable_bm25: bool = True

    @classmethod
    def from_env(cls) -> "MemoryConfig":
        """Create config from environment variables."""
        default_path = str(Path.home() / ".claude" / "memories" / "memory.db")

        return cls(
            db_path=os.getenv("MEMORY_DB_PATH", default_path),
            collection_name=os.getenv("MEMORY_COLLECTION_NAME", "claude_memories"),
            embedding_model=os.getenv("MEMORY_EMBEDDING_MODEL", "intfloat/multilingual-e5-base"),
            enable_bm25=os.getenv("MEMORY_ENABLE_BM25", "true").lower() != "false",
        )


@dataclass(frozen=True)
class ServerConfig:
    """MCP Server configuration."""

    name: str = "memory-mcp"
    version: str = "0.4.6"

    @classmethod
    def from_env(cls) -> "ServerConfig":
        """Create config from environment variables."""
        return cls(
            name=os.getenv("MCP_SERVER_NAME", "memory-mcp"),
            version=os.getenv("MCP_SERVER_VERSION", "0.4.6"),
        )


@dataclass(frozen=True)
class SleepConfig:
    """Sleep (nightly consolidation, decay and forgetting) settings.

    Adapted from PetitOnes/m5-petit-memory (Apache License 2.0), where the
    PetitOnes run it for their three characters every night at 04:05.
    """

    min_age_days: int = 14  # memories younger than this are left alone
    similarity_threshold: float = 0.85  # merge memories of one category this similar
    decay_retention_threshold: float = 0.4  # below this retention score, importance goes down by one
    forget_min_age_days: int = 14
    forget_max_access: int = 3  # recalled this often or more: not forgotten
    protected_importance: int = 4  # this important or more: never touched
    protected_emotions: tuple[str, ...] = ("happy", "moved", "excited", "surprised")
