"""Enterprise RAG LangGraph compiled subagent configuration."""

from app.agent.prompts import sub_agents_content
from app.rag.graph import rag_graph

# DeepAgents 0.5.7 accepts a pre-compiled Runnable as a CompiledSubAgent.
# The RAG graph state contains `messages`, which is required for returning the
# final evidence message to the parent agent.
knowledge_base_agent = {
    "name": sub_agents_content["ragflow"]["name"],
    "description": sub_agents_content["ragflow"]["description"],
    "runnable": rag_graph,
}
