import logging
import sys
import mcp_memory

from typing import Dict, Optional
from mcp.server.fastmcp import FastMCP 

logging.basicConfig(
    level=logging.INFO,  # Default to INFO level
    format='%(filename)s:%(lineno)d | %(message)s',
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)
logger = logging.getLogger("memory")

try:
    mcp = FastMCP(
        name = "memory"
    )
    logger.info("MCP server initialized successfully")
except Exception as e:
        err_msg = f"Error: {str(e)}"
        logger.info(f"{err_msg}")

######################################
# memory
######################################
@mcp.tool()
def recall_memory(
    action: str,
    query: Optional[str] = None,
    memory_record_id: Optional[str] = None,
    max_results: Optional[int] = None,
    next_token: Optional[str] = None,
) -> Dict:
    """
    Recall agent memories including user profile preferences.

    This tool helps agents access long-term memories and the user's profile
    (preferences, style, facts extracted from past conversations).

    Key Capabilities:
    - Semantic search across conversation memories and user profile
    - Browse and list all stored memories (including user profile records)
    - Retrieve specific memories by ID

    Supported Actions:
    -----------------
    - retrieve: Find relevant memories using semantic search.
        Searches both general long-term memories and the user profile namespace.
        Best for queries like "find memories about X" or "what are the user's preferences".

    - list: Browse all stored memories including user profile records.

    - get: Fetch a specific memory by ID.

    Args:
        action: The memory operation to perform (one of: "retrieve", "list", "get")
        query: Search terms for finding relevant memories / user profile (required for retrieve)
        memory_record_id: ID of a specific memory (required for get action)
        max_results: Maximum number of results to return (optional)
        next_token: Pagination token (optional)

    Returns:
        Dict: Response containing the requested memory information or operation status
    """
    logger.info(f"###### recall_memory ######")
    logger.info(f"action: {action}")

    return mcp_memory.recall_memory(action, query, memory_record_id, max_results, next_token)

if __name__ =="__main__":
    mcp.run(transport="stdio")
