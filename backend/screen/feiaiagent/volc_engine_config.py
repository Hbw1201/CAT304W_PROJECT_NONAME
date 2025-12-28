# -*- coding: utf-8 -*-
"""
火山引擎认证配置
请妥善保管这些敏感信息，不要提交到版本控制系统
"""

import os
from typing import Dict


class VolcEngineConfig:
    """火山引擎配置类"""
    
    # APP ID
    APP_ID = "1835634118"
    
    # Access Token
    ACCESS_TOKEN = "tf9o_FYwJs0spaVoZXi7AUFzNaiyBkuK"
    
    # Secret Key
    SECRET_KEY = "u8zlWsEmf8tKu1HbcJtZh-dwU3Y3z5Oc"
    
    @classmethod
    def get_app_id(cls) -> str:
        """获取 APP ID"""
        return cls.APP_ID
    
    @classmethod
    def get_access_token(cls) -> str:
        """获取 Access Token"""
        return cls.ACCESS_TOKEN
    
    @classmethod
    def get_secret_key(cls) -> str:
        """获取 Secret Key"""
        return cls.SECRET_KEY
    
    @classmethod
    def get_all_credentials(cls) -> Dict[str, str]:
        """获取所有认证信息"""
        return {
            "app_id": cls.APP_ID,
            "access_token": cls.ACCESS_TOKEN,
            "secret_key": cls.SECRET_KEY
        }
    
    @classmethod
    def validate_config(cls) -> bool:
        """验证配置是否完整"""
        return all([
            cls.APP_ID,
            cls.ACCESS_TOKEN,
            cls.SECRET_KEY
        ])
    
    @classmethod
    def get_config_info(cls) -> Dict[str, str]:
        """获取配置信息（隐藏敏感信息）"""
        return {
            "app_id": cls.APP_ID,
            "access_token": cls.ACCESS_TOKEN[:10] + "..." if cls.ACCESS_TOKEN else "",
            "secret_key": cls.SECRET_KEY[:10] + "..." if cls.SECRET_KEY else "",
            "is_valid": str(cls.validate_config())
        }


# 使用示例：
if __name__ == "__main__":
    config = VolcEngineConfig()
    print("火山引擎配置信息：")
    print(f"APP ID: {config.get_app_id()}")
    print(f"Access Token: {config.get_access_token()}")
    print(f"Secret Key: {config.get_secret_key()}")
    print(f"配置验证: {config.validate_config()}")
    print(f"配置信息: {config.get_config_info()}")


