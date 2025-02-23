import os
from langchain.schema import HumanMessage, SystemMessage, BaseMessage
from custom_types.api_operation import ApiOperation_vs
from custom_types.bot_message import BotMessage
from routes.workflow.extractors.extract_json import extract_json_payload

# push it to the library
from opencopilot_utils.get_vector_store import get_vector_store
from opencopilot_utils import StoreOptions
from integrations.custom_prompts.prompt_loader import load_prompts
from models.repository.chat_history_repo import get_chat_message_as_llm_conversation
from utils.chat_models import CHAT_MODELS
from utils import get_chat_model
from typing import Optional, List, cast, Any
from langchain.vectorstores.base import VectorStore
from utils.get_logger import struct_log

chat = get_chat_model(CHAT_MODELS.gpt_3_5_turbo_16k)

knowledgebase: VectorStore = get_vector_store(StoreOptions("knowledgebase"))
apis: VectorStore = get_vector_store(StoreOptions("apis"))


def get_relevant_docs(text: str, bot_id: str) -> Optional[str]:
    try:
        score_threshold = float(os.getenv("SCORE_THRESHOLD_KB", "0.50"))

        kb_retriever = knowledgebase.as_retriever(
            search_kwargs={
                "k": 3,
                "score_threshold": score_threshold,
                "filter": {"bot_id": bot_id},
            },
        )

        result = kb_retriever.get_relevant_documents(text)

        if result and len(result) > 0:
            # Assuming result is a list of objects and each object has a page_content attribute
            all_page_content = "\n\n".join([item.page_content for item in result])

            return all_page_content

        return None

    except Exception as e:
        struct_log.exception(payload=text, error=str(e), event="get_relevant_docs")
        return None


def get_relevant_apis_summaries(text: str, bot_id: str) -> List[Any]:
    try:
        score_threshold = float(os.getenv("SCORE_THRESHOLD_KB", "0.75"))

        apis_retriever = apis.as_retriever(
            search_kwargs={
                "k": 5,
                "score_threshold": score_threshold,
                "filter": {"bot_id": bot_id},
            },
        )

        results = apis_retriever.get_relevant_documents(text)

        resp: List[ApiOperation_vs] = []
        for result in results:
            resp.append(result.metadata["operation"])

        return resp

    except Exception as e:
        struct_log.exception(
            payload=text, error=str(e), event="get_relevant_apis_summaries"
        )
        return []


# @Remaining tasks here
# 1. Todo: add application initial state here as well
# 2. Add api data from qdrant so that the llm understands what apis are available to it for use
def process_conversation_step(
    session_id: str,
    app: Optional[str],
    user_requirement: str,
    context: Optional[str],
    api_summaries: Optional[str],
):
    if not session_id:
        raise ValueError("Session id must be defined for chat conversations")

    prev_conversations = get_chat_message_as_llm_conversation(session_id)
    prompt_templates = load_prompts(app)
    system_message_classifier = SystemMessage(
        content="You are a helpful ai assistant. User will give you two things, a list of api's and some useful information, called context. "
    )
    if app and prompt_templates:
        system_message_classifier = prompt_templates.system_message_classifier
    struct_log.info(
        event="system_message_classifier",
        app=app,
        context=context,
        classification_prompt=system_message_classifier,
        prev_conversations=prev_conversations,
    )
    messages: List[BaseMessage] = []
    messages.append(system_message_classifier)

    if context and api_summaries:
        messages.append(
            HumanMessage(
                content=f"Here is some relevant context I found that might be helpful - ```{context}```. Also, here is the excerpt from API swagger for the APIs I think might be helpful in answering the question ```{api_summaries}```. "
            )
        )
    elif context:
        messages.append(
            HumanMessage(
                content=f"I found some relevant context that might be helpful. Here is the context: ```{context}```. "
            )
        )
    elif api_summaries:
        messages.append(
            HumanMessage(
                content=f"I found API summaries that might be helpful in answering the question. Here are the api summaries: ```{api_summaries}```. "
            )
        )
    else:
        pass

    messages.append(
        HumanMessage(
            content="""Based on the information provided to you I want you to answer the questions that follow. Your should respond with a json that looks like the following - 
    {{
        "ids": ["list", "of", "apis", "to", "be", "called"],
        "bot_message": "your response based on the instructions provided at the beginning"
    }}                
    """
        )
    )
    messages.append(
        HumanMessage(content="If you are unsure / confused, ask claryfying questions")
    )

    messages.append(HumanMessage(content=user_requirement))

    if len(prev_conversations) > 0:
        messages.extend(prev_conversations)

    content = chat(messages=messages).content
    d = extract_json_payload(cast(str, content))

    if isinstance(d, str):
        return BotMessage(ids=[], bot_message=d)

    struct_log.info(event="extract_json_payload", data=d)

    bot_message = BotMessage.from_dict(d)
    return bot_message
