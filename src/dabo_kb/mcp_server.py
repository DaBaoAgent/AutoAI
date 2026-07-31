from __future__ import annotations

from fastmcp import FastMCP

from .cli import status
from .graph import related
from .search import document, search
from .vector import semantic_search


mcp = FastMCP(
    "dabo-local-kb",
    instructions="检索大宝的本地 AI 收藏、逐字稿、项目资料和知识图谱，并返回可追溯来源。",
)


@mcp.tool
def search_knowledge(query: str, limit: int = 8, semantic: bool = False) -> list[dict]:
    """检索大宝的本地 AI 知识库；semantic=true 时使用本地语义索引。"""
    return semantic_search(query, limit) if semantic else search(query, limit)


@mcp.tool
def get_source(source_id: str) -> dict | None:
    """按抖音视频 ID 读取完整来源记录。"""
    return document(source_id)


@mcp.tool
def related_entities(name: str, limit: int = 10) -> list[dict]:
    """查询项目、工具、概念或标签在知识图谱中的相关实体。"""
    return related(name, limit)


@mcp.tool
def knowledge_status() -> dict:
    """查看知识库文档、逐字稿和索引进度。"""
    return status()


if __name__ == "__main__":
    mcp.run()

