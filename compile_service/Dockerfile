# compile_service/Dockerfile
# 基础镜像需含 msbuild(金蝶 BOS 为 .NET Framework 4.x,优先 Windows 容器或 mono 兼容层)
FROM mcr.microsoft.com/dotnet/framework/sdk:4.8-windowsservercore-ltsc2022
WORKDIR /app
COPY . .
# 金蝶 BOS DLL 放 build/references/(团队提供,授权合规)
COPY build/references/ ./references/
ENV COMPILE_SERVICE_REQUIRES_DLLS=1
# 容器布局:references 固定于 /app/references(上方 COPY 目标),显式声明 REFS_DIR,
# 避免代码相对默认值(compile_service/build/references)在容器内接管 —— 服务端代码零硬编码路径,容器契约走 env
ENV REFS_DIR=/app/references
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "compile_service.server:create_factory", "--host", "0.0.0.0", "--port", "8000"]
