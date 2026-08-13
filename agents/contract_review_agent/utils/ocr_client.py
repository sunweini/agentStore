"""百度智能云 OCR 客户端(云端接口,零本地模型)。

凭据:.env 的 BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY。
"""
from __future__ import annotations

import base64

import httpx

_TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
_OCR_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic"


def get_baidu_token(api_key: str, secret_key: str) -> str:
    resp = httpx.post(_TOKEN_URL, params={
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": secret_key,
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def ocr_image_bytes(img: bytes, token: str) -> str:
    resp = httpx.post(_OCR_URL, params={"access_token": token},
                      data={"image": base64.b64encode(img)}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    words = [w["words"] for w in data.get("words_result", [])]
    return " ".join(words)


def ocr_pdf_pages(pdf_path: str, token: str) -> str | None:
    """PDF 逐页渲染 OCR。依赖 PyMuPDF(fitz),未安装时返回 None。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    texts: list[str] = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            texts.append(ocr_image_bytes(pix.tobytes("png"), token))
    return "\n".join(texts)
