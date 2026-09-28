"""
飞书表格客户端
将推广申请数据写入飞书表格
"""
import time
import requests
import json
from typing import List, Dict, Optional


class LarkClient:
    """飞书多维表格客户端"""
    
    def __init__(self, app_id: str = "", app_secret: str = "", app_token: str = ""):
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self._tenant_token = None
        self._base_url = "https://open.feishu.cn/open-apis"

    def set_config(self, app_id: str, app_secret: str, app_token: str):
        """更新配置"""
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self._tenant_token = None

    def _retry_request(self, method: str, url: str, retries: int = 3, **kwargs) -> requests.Response:
        """
        带指数退避的请求封装：容忍网络抖动与飞书服务端 5xx/限流。
        业务错误（4xx、code!=0）不在此重试，保持原有语义由调用方处理。
        """
        last_exc = None
        for attempt in range(retries):
            try:
                resp = requests.request(method, url, timeout=kwargs.pop("timeout", 15), **kwargs)
                if resp.status_code >= 500:
                    raise Exception(f"HTTP {resp.status_code}")
                return resp
            except Exception as e:
                last_exc = e
                print(f"[Lark] {method} {url[:80]} 失败（第{attempt+1}/{retries}次）: {e}")
                if attempt < retries - 1:
                    time.sleep(1 * (2 ** attempt))  # 1s / 2s 退避
        raise last_exc

    def _get_tenant_token(self) -> str:
        """获取 tenant_access_token，自动从配置文件读最新配置"""
        # 每次都从配置文件读，保证配置最新
        import os
        config_file = os.path.join(os.path.dirname(__file__), "data", "config.json")
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                config = json.load(f)
            self.app_id = config.get("app_id", self.app_id)
            self.app_secret = config.get("app_secret", self.app_secret)
            self.app_token = config.get("app_token", self.app_token)
            self._tenant_token = None  # 配置变了，清空缓存
        
        if self._tenant_token:
            return self._tenant_token

        url = f"{self._base_url}/auth/v3/tenant_access_token/internal"
        resp = self._retry_request("post", url, json={
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        })
        data = resp.json()
        if data.get("code") == 0:
            self._tenant_token = data["tenant_access_token"]
            return self._tenant_token
        raise Exception(f"获取tenant_token失败: {data.get('msg')}, app_id={self.app_id}")

    def _get_headers(self) -> dict:
        """获取请求头"""
        token = self._get_tenant_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def list_tables(self) -> List[Dict]:
        """列出所有数据表"""
        url = f"{self._base_url}/bitable/v1/apps/{self.app_token}/tables"
        resp = self._retry_request("get", url, headers=self._get_headers())
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data", {}).get("items", [])
        raise Exception(f"列出表格失败: {data.get('msg')}")

    def add_record(self, table_id: str, fields: Dict) -> Dict:
        """添加一条记录"""
        url = f"{self._base_url}/bitable/v1/apps/{self.app_token}/tables/{table_id}/records"
        resp = self._retry_request("post", url, headers=self._get_headers(), json={
            "fields": fields
        })
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data", {}).get("record", {})
        raise Exception(f"添加记录失败: {data.get('msg')}")

    def add_records_batch(self, table_id: str, records: List[Dict]) -> List[Dict]:
        """批量添加记录"""
        if not records:
            return []

        url = f"{self._base_url}/bitable/v1/apps/{self.app_token}/tables/{table_id}/records/batch_create"
        body = {
            "records": [{"fields": r} for r in records]
        }
        resp = self._retry_request("post", url, headers=self._get_headers(), json=body)
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data", {}).get("records", [])
        raise Exception(f"批量添加记录失败: {data.get('msg')}")

    def test_connection(self) -> Dict:
        """测试连接"""
        try:
            token = self._get_tenant_token()
            return {"success": True, "message": "连接成功"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def send_message(self, chat_id: str, text: str) -> Dict:
        """发送文本消息到群聊"""
        url = f"{self._base_url}/im/v1/messages?receive_id_type=chat_id"
        body = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text})
        }
        resp = self._retry_request("post", url, headers=self._get_headers(), json=body)
        data = resp.json()
        if data.get("code") == 0:
            return {"success": True}
        raise Exception(f"发送消息失败: {data.get('msg')}")
