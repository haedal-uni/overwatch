# overwatch

<img src="https://github.com/user-attachments/assets/63cb5eb5-4275-46b8-9959-a327f0b25656" />


## Install
```bash
Set-ExecutionPolicy RemoteSigned -scope CurrentUser

powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

uv add python-dotenv langchain-chroma langchain-community langchain-core langchain-text-splitters langchain-huggingface sentence-transformers langchain-google-genai
```

### Django
```bash
uv init --python 3.11
uv add django
uv run django-admin startproject config .
uv run python manage.py startapp chat
uv run python manage.py runserver
```





