import logging
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_community.utilities import SQLDatabase
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
import gradio as gr

# ----------------- Logging Setup -----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("chat_with_mysql.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ----------------- Env -----------------
load_dotenv()

# ----------------- Prompt Templates -----------------
SQL_GENERATION_PROMPT = """
You are a data analyst at a company. You are interacting with a user who is asking you questions about the company's database.
Based on the table schema below, write a SQL query that would answer the user's question. Take the conversation history into account.

<SCHEMA>{schema}</SCHEMA>

Conversation History: {chat_history}

Write only the SQL query and nothing else. Do not wrap the SQL query in any other text, not even backticks.

Example:
Question: which 3 artists have the most tracks?
SQL Query: SELECT ArtistId, COUNT(*) as track_count FROM Track GROUP BY ArtistId ORDER BY track_count DESC LIMIT 3;
Question: Name 10 artists
SQL Query: SELECT Name FROM Artist LIMIT 10;

Your turn:

Question: {question}
SQL Query:
"""

NL_RESPONSE_PROMPT = """
You are a data analyst at a company. You are interacting with a user who is asking you questions about the company's database.
Based on the table schema below, question, sql query, and sql response, write a natural language response.

<SCHEMA>{schema}</SCHEMA>
Conversation History: {chat_history}
SQL Query: <SQL>{query}</SQL>
User question: {question}
SQL Response: {response}
"""

# ----------------- Database Functions -----------------
def init_database(user: str, password: str, host: str, port: str, database: str) -> SQLDatabase:
    """Initializes and returns a SQLDatabase object."""
    db_uri = f"mysql+mysqlconnector://{user}:{password}@{host}:{port}/{database}"
    logger.info(f"Connecting to DB: {user}@{host}:{port}/{database}")
    return SQLDatabase.from_uri(db_uri)


def get_sql_chain(db: SQLDatabase):
    """Creates a LangChain SQL generation chain."""
    prompt = ChatPromptTemplate.from_template(SQL_GENERATION_PROMPT)
    llm = ChatOpenAI(model="gpt-4o-mini")

    return (
        RunnablePassthrough.assign(schema=lambda _: db.get_table_info())
        | prompt
        | llm
        | StrOutputParser()
    )


def get_response(user_query: str, db: SQLDatabase, chat_history: list) -> str:
    """Generates a natural language response to the user's query."""
    sql_chain = get_sql_chain(db)
    prompt = ChatPromptTemplate.from_template(NL_RESPONSE_PROMPT)
    llm = ChatOpenAI(model="gpt-4o-mini")

    chain = (
        RunnablePassthrough.assign(query=sql_chain).assign(
            schema=lambda _: db.get_table_info(),
            response=lambda vars: db.run(vars["query"]),
        )
        | prompt
        | llm
        | StrOutputParser()
    )

    try:
        result = chain.invoke({"question": user_query, "chat_history": chat_history})
        logger.info(f"User: {user_query}")
        logger.info(f"Generated SQL: {chat_history[-1].content if chat_history else 'N/A'}")
        logger.info(f"Response: {result}")
        return result
    except Exception as e:
        logger.error(f"Error processing query: {e}")
        return f"⚠️ Error processing query: {e}"

# ----------------- Gradio Functions -----------------
def connect_to_db(host: str, port: str, user: str, password: str, database: str):
    """Connect to the database and return status + db object."""
    try:
        db = init_database(user, password, host, port, database)
        logger.info("✅ Database connection successful")
        return "✅ Connected to database!", db
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        return f"❌ Failed to connect: {e}", None


def chat_interface_predict(message: str, history: list, db: SQLDatabase):
    """Chat predictor for Gradio ChatInterface."""
    if db is None:
        return "⚠️ Please connect to the database first."

    # Convert Gradio chat history → LangChain messages
    langchain_chat_history = []
    for human_msg, ai_msg in history:
        if human_msg:
            langchain_chat_history.append(HumanMessage(content=human_msg))
        if ai_msg:
            langchain_chat_history.append(AIMessage(content=ai_msg))

    langchain_chat_history.append(HumanMessage(content=message))

    return get_response(message, db, langchain_chat_history)

# ----------------- Gradio UI -----------------
with gr.Blocks(title="Chat with MySQL") as demo:
    gr.Markdown(
        """
        # 💬 Chat with MySQL  
        Connect to your MySQL database and ask questions in plain English.  
        The assistant will generate SQL, run it, and explain results in natural language. 🚀
        """
    )

    db_state = gr.State(None)

    with gr.Row():
        # Sidebar Settings
        with gr.Column(scale=1):
            with gr.Accordion("⚙️ Database Settings", open=True):
                host_input = gr.Textbox(label="Host", value="localhost")
                port_input = gr.Textbox(label="Port", value="3306")
                user_input = gr.Textbox(label="User", value="root")
                password_input = gr.Textbox(label="Password", type="password", value="admin")
                database_input = gr.Textbox(label="Database", value="Chinook")

                connect_button = gr.Button("🔗 Connect to Database")
                connection_status = gr.Markdown("❌ Not connected.")

                connect_button.click(
                    fn=connect_to_db,
                    inputs=[host_input, port_input, user_input, password_input, database_input],
                    outputs=[connection_status, db_state],
                )

        # Main Chat
        with gr.Column(scale=3):
            gr.ChatInterface(
                fn=lambda msg, hist: chat_interface_predict(msg, hist, db_state.value),
                examples=[
                    ["How many artists are there?"],
                    ["List the top 5 longest tracks."],
                    ["What is the average price of a track?"],
                ],
                title="🎵 SQL Assistant",
                chatbot=gr.Chatbot(height=500, label="Database Assistant"),
            )

    # Custom Styling
    demo.css = """
    #connection_status {
        font-weight: bold;
    }
    .green { color: green; }
    .red { color: red; }
    """

demo.launch(share=True)
