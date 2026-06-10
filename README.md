# overwatch
<img src="https://github.com/user-attachments/assets/c030ad6e-f72c-4d74-b179-cd50d09c6a21" />

<img  src="https://github.com/user-attachments/assets/acfbacd5-a75b-4b00-8896-78ed65cdd140" />

<img src="https://github.com/user-attachments/assets/7fdf530c-e172-4a83-a95a-9d134feb20ed" />


<br><br>

<img src="https://github.com/user-attachments/assets/ab24fbb5-6618-4e7b-9c6f-7d8cdfe4029a" />

<br><br>

## Install
```bash
Set-ExecutionPolicy RemoteSigned -scope CurrentUser

powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

uv add python-dotenv langchain-chroma langchain-community langchain-core langchain-text-splitters langchain-huggingface sentence-transformers langchain-google-genai
```

### Django
```bash
uv init --python 3.11
uv add django langgraph
uv run django-admin startproject config .
uv run python manage.py startapp chat
uv run python manage.py runserver
```





