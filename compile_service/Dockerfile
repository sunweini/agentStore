# compile_service/Dockerfile
# 基础镜像需含 msbuild(金蝶 BOS 为 .NET Framework 4.x,优先 Windows 容器或 mono 兼容层)
FROM mcr.microsoft.com/dotnet/framework/sdk:4.8-windowsservercore-ltsc2022
WORKDIR /app
COPY . .
# 金蝶 BOS DLL 放 build/references/(团队提供,授权合规)
COPY build/references/ ./references/
ENV COMPILE_SERVICE_REQUIRES_DLLS=1
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "compile_service.server:create_factory", "--host", "0.0.0.0", "--port", "8000"]
