"""Skill vocabulary shared by scoring, tailoring and the weekly gap report.

Each canonical skill has a category (used to group the tailored Skills section)
and aliases matched on word boundaries. Aliases are lowercase.
"""
import re

TAXONOMY = {
    # Languages
    'Python': ('Languages', ['python']),
    'Java': ('Languages', ['java']),
    'JavaScript': ('Languages', ['javascript', 'node.js', 'nodejs', 'node']),
    'TypeScript': ('Languages', ['typescript']),
    'Go': ('Languages', ['golang']),
    'Rust': ('Languages', ['rust']),
    'C++': ('Languages', ['c++']),
    'C#': ('Languages', ['c#', '.net']),
    'Scala': ('Languages', ['scala']),
    'SQL': ('Languages', ['sql']),
    'Bash': ('Languages', ['bash', 'shell scripting']),
    # GenAI / LLM
    'LLMs': ('GenAI & LLMs', ['llm', 'llms', 'large language model', 'large language models', 'foundation models', 'foundation model']),
    'Generative AI': ('GenAI & LLMs', ['generative ai', 'genai', 'gen ai']),
    'RAG': ('GenAI & LLMs', ['rag', 'retrieval augmented generation', 'retrieval-augmented generation']),
    'AI Agents': ('GenAI & LLMs', ['agentic', 'multi-agent', 'ai agents', 'ai agent', 'agent orchestration', 'autonomous agents', 'agent frameworks', 'agents']),
    'Prompt Engineering': ('GenAI & LLMs', ['prompt engineering', 'prompt design', 'prompting']),
    'Context Engineering': ('GenAI & LLMs', ['context engineering']),
    'Fine-tuning': ('GenAI & LLMs', ['fine-tuning', 'fine tuning', 'finetuning', 'lora', 'qlora', 'peft', 'sft', 'rlhf', 'dpo']),
    'LLM Evaluation': ('GenAI & LLMs', ['evaluation', 'evals', 'eval harness', 'llm-as-judge', 'llm as a judge', 'evaluation frameworks', 'ragas', 'deepeval']),
    'Guardrails': ('GenAI & LLMs', ['guardrails', 'prompt injection', 'jailbreak', 'content moderation', 'red teaming', 'red-teaming']),
    'Function Calling': ('GenAI & LLMs', ['function calling', 'tool calling', 'tool use', 'structured outputs']),
    'MCP': ('GenAI & LLMs', ['mcp', 'model context protocol']),
    'Embeddings': ('GenAI & LLMs', ['embeddings', 'embedding models', 'semantic search']),
    'Vector Databases': ('GenAI & LLMs', ['vector database', 'vector databases', 'vector search', 'vector store', 'pinecone', 'weaviate', 'qdrant', 'milvus', 'chroma', 'pgvector', 'faiss']),
    'LangChain': ('GenAI & LLMs', ['langchain']),
    'LangGraph': ('GenAI & LLMs', ['langgraph']),
    'LlamaIndex': ('GenAI & LLMs', ['llamaindex', 'llama index', 'llama_index']),
    'CrewAI': ('GenAI & LLMs', ['crewai', 'autogen']),
    'DSPy': ('GenAI & LLMs', ['dspy']),
    'OpenAI API': ('GenAI & LLMs', ['openai', 'gpt-4', 'gpt-4o', 'chatgpt']),
    'Claude': ('GenAI & LLMs', ['claude', 'anthropic']),
    'Gemini': ('GenAI & LLMs', ['gemini', 'vertex ai']),
    'Open-source LLMs': ('GenAI & LLMs', ['llama', 'mistral', 'hugging face', 'huggingface', 'transformers', 'vllm', 'ollama']),
    'LLMOps': ('GenAI & LLMs', ['llmops', 'langsmith', 'langfuse', 'mlflow', 'weights & biases', 'wandb', 'arize', 'phoenix']),
    'Conversational AI': ('GenAI & LLMs', ['chatbot', 'chatbots', 'conversational ai', 'dialogflow', 'voice agents', 'speech-to-text', 'text-to-speech']),
    # ML
    'Machine Learning': ('ML & Data', ['machine learning', 'ml models', 'scikit-learn', 'sklearn', 'xgboost']),
    'Deep Learning': ('ML & Data', ['deep learning', 'neural networks']),
    'PyTorch': ('ML & Data', ['pytorch']),
    'TensorFlow': ('ML & Data', ['tensorflow', 'keras']),
    'NLP': ('ML & Data', ['nlp', 'natural language processing', 'spacy', 'nltk']),
    'Computer Vision': ('ML & Data', ['computer vision', 'opencv', 'image recognition']),
    'MLOps': ('ML & Data', ['mlops', 'model serving', 'model deployment', 'sagemaker', 'kubeflow']),
    'Pandas': ('ML & Data', ['pandas', 'numpy']),
    'Spark': ('ML & Data', ['spark', 'pyspark', 'databricks']),
    'Airflow': ('ML & Data', ['airflow', 'dagster', 'prefect']),
    'Kafka': ('ML & Data', ['kafka', 'kinesis', 'pub/sub']),
    'Data Pipelines': ('ML & Data', ['data pipelines', 'etl', 'elt', 'data engineering']),
    'Snowflake': ('ML & Data', ['snowflake', 'bigquery', 'redshift']),
    # Backend / frameworks
    'FastAPI': ('Backend & APIs', ['fastapi']),
    'Flask': ('Backend & APIs', ['flask']),
    'Django': ('Backend & APIs', ['django']),
    'Spring Boot': ('Backend & APIs', ['spring boot', 'spring']),
    'REST APIs': ('Backend & APIs', ['rest', 'restful', 'rest api', 'rest apis', 'api design', 'apis']),
    'GraphQL': ('Backend & APIs', ['graphql']),
    'gRPC': ('Backend & APIs', ['grpc']),
    'Microservices': ('Backend & APIs', ['microservices', 'microservice', 'distributed systems', 'event-driven']),
    'Async Python': ('Backend & APIs', ['asyncio', 'async python', 'celery']),
    # Frontend
    'React': ('Frontend', ['react', 'react.js', 'reactjs']),
    'Next.js': ('Frontend', ['next.js', 'nextjs']),
    'HTML/CSS': ('Frontend', ['html', 'css', 'tailwind']),
    'Streamlit': ('Frontend', ['streamlit', 'gradio']),
    # Data stores
    'PostgreSQL': ('Databases', ['postgresql', 'postgres']),
    'MySQL': ('Databases', ['mysql']),
    'MongoDB': ('Databases', ['mongodb', 'nosql', 'dynamodb']),
    'Redis': ('Databases', ['redis']),
    'Elasticsearch': ('Databases', ['elasticsearch', 'opensearch']),
    # Cloud & DevOps
    'AWS': ('Cloud & DevOps', ['aws', 'amazon web services', 'lambda', 'ec2', 's3', 'ecs']),
    'Bedrock': ('Cloud & DevOps', ['bedrock', 'amazon bedrock']),
    'Azure': ('Cloud & DevOps', ['azure', 'azure openai']),
    'GCP': ('Cloud & DevOps', ['gcp', 'google cloud']),
    'Docker': ('Cloud & DevOps', ['docker', 'containers', 'containerization']),
    'Kubernetes': ('Cloud & DevOps', ['kubernetes', 'k8s', 'helm', 'eks', 'gke']),
    'Terraform': ('Cloud & DevOps', ['terraform', 'infrastructure as code', 'iac', 'cloudformation', 'cdk']),
    'CI/CD': ('Cloud & DevOps', ['ci/cd', 'github actions', 'continuous integration', 'jenkins', 'gitlab ci']),
    'Observability': ('Cloud & DevOps', ['observability', 'tracing', 'opentelemetry', 'datadog', 'prometheus', 'grafana', 'monitoring']),
    'Linux': ('Cloud & DevOps', ['linux', 'unix']),
    'Git': ('Cloud & DevOps', ['git', 'github']),
    'Serverless': ('Cloud & DevOps', ['serverless']),
    # Practices
    'Testing': ('Practices', ['pytest', 'unit testing', 'integration testing', 'test automation', 'tdd']),
    'System Design': ('Practices', ['system design', 'scalable systems', 'scalability']),
    'Security': ('Practices', ['security', 'oauth', 'authentication', 'soc 2', 'pii']),
    'Agile': ('Practices', ['agile', 'scrum']),
    'Customer-facing': ('Practices', ['customer-facing', 'client-facing', 'stakeholders', 'solutions engineering', 'pre-sales', 'forward deployed']),
}

CATEGORY_ORDER = ['GenAI & LLMs', 'Languages', 'Backend & APIs', 'ML & Data', 'Cloud & DevOps',
                  'Databases', 'Frontend', 'Practices']

# 'go', 'rest', 'node', 'agents', 'spring', 'lambda', 'rust' and similar short words
# create false positives in prose; require nearby technical context.
AMBIGUOUS = {'rest', 'node', 'agents', 'spring', 'lambda', 'rust', 'apis', 'git', 'llama',
             'mistral', 'gemini', 'phoenix', 'security', 'monitoring', 'evaluation', 'prompting',
             'containers', 's3', 'ecs', 'chroma', 'transformers', 'scala', 'claude', 'anthropic'}
_TECH_CONTEXT = re.compile(r'\b(api|apis|framework|python|cloud|llm|model|models|service|services|sdk|deploy|stack|engineer|code|data|aws)\b', re.I)

_ALIAS_TO_SKILL = {alias: skill for skill, (_, aliases) in TAXONOMY.items() for alias in aliases}
# One alternation, longest alias first, so each text is scanned once.
_ANY = re.compile(r'(?<![\w.+#-])(' + '|'.join(re.escape(a) for a in sorted(_ALIAS_TO_SKILL, key=len, reverse=True)) + r')(?![\w+#])', re.I)


def category(skill):
    return TAXONOMY.get(skill, ('Other', []))[0]


def _matches(text):
    for m in _ANY.finditer(text or ''):
        alias = m.group(1).lower()
        if alias in AMBIGUOUS:
            window = text[max(0, m.start() - 80):m.end() + 80]
            if not _TECH_CONTEXT.search(window):
                continue
        yield _ALIAS_TO_SKILL[alias], m.group(1)


def find_skills(text):
    """Return {canonical skill: first matching alias as written in the text}."""
    found = {}
    for skill, written in _matches(text):
        found.setdefault(skill, written)
    return found


def skill_counts(text):
    """How often each canonical skill appears; used to weight JD keywords."""
    counts = {}
    for skill, _ in _matches(text):
        counts[skill] = counts.get(skill, 0) + 1
    return counts


def load_target_titles(path):
    """Parse docs/TARGET-ROLES.md into a list of title strings."""
    titles = []
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            m = re.match(r'\s*(?:\d+\.\s+|Primary title:\s*\*\*)(.+?)(?:\*\*)?\s*$', line)
            if m and len(m[1]) < 80:
                titles.append(m[1].strip())
    except OSError:
        pass
    return titles or ['AI Engineer']


def title_aliases(titles):
    """Concrete search aliases. 'Software Engineer (AI / GenAI / LLM)' yields 'ai software engineer',
    'genai software engineer'... but never the bare, generic 'software engineer'."""
    out = set()
    for t in titles:
        parts, depth, buf = [], 0, ''
        for ch in t:  # split on '/' only outside parentheses
            depth += ch == '('
            depth -= ch == ')'
            if ch == '/' and depth == 0:
                parts.append(buf)
                buf = ''
            else:
                buf += ch
        parts.append(buf)
        for part in parts:
            part = part.strip()
            m = re.search(r'\((.*?)\)', part)
            base = re.sub(r'\s*\(.*?\)', '', part).strip().lower()
            if m:
                for q in re.split(r'\s*/\s*', m[1]):
                    out.add(f'{q.strip().lower()} {base}')
            elif base:
                out.add(base)
    return sorted(out)
