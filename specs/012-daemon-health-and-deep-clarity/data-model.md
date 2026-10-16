# Data model

**ServerRecord** (`$CAIRN_HOME/server.json`): `pid:int`, `port:int`, `started:float`, `code_root:path`, `version:str`, `python:path`, `path:str`.
**ServerVerdict** (computed): `stale: null | "code-missing" | "version-differs" | "python-missing"`, `fix:str`.
**DeepState** (computed): `enabled:bool`, `backend:"anthropic"|"openai"|"claude-code"|null`, `reason:str`, `fix:str`, `claude_path:path|null`.
**Budget** (computed): `key:str`, `name:str`, `value:int`, `unit:"tokens"|"seconds"`, `used:int|null`, `meaning:str`, `when_exceeded:str`.
