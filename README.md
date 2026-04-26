管理端包含知识库搭建（上传文档，解析文档，添加到向量库），按权限查询向量库，文档管理和用户管理数据库使用的sqlite
文档支持doc/docx/pdf/xls/xlsx/md/txt
暂不支持图片类型的pdf识别

用户端包含知识库、本地工具查询，MCP服务端、客户端功能

创建虚拟环境


conda create -n rag python=3.10


conda activate rag


安装必要的包


pip install -r requirements.txt


向量库


docker run -itd -p 6333:6333 qdrant/qdrant

使用阿里百炼的apikey，使用LLM等模型


echo "DASHSCOPE_API_KEY=sk-xxx" > .env

启动项目
管理端（8001）、MCP（8003）、客户端（8002）


nohup uvicorn admin.main:app --host 0.0.0.0 --reload --port 8001 > admin_log.out &

nohup python -m mcp_server.server --transport sse --port 8003 > mcp_log.out &

nohup uvicorn chat.main:app --host 0.0.0.0 --reload --port 8002 > chat_log.out &

使用

http://localhost:8001/


http://localhost:8002/
