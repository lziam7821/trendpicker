"""TrendPicker: 数据驱动的电商选品流水线.

模块分层:
    - credentials : 凭据管理 (Keychain + dotenv)
    - features    : 特征工程
    - label       : 爆款标签构造
    - ingestion  : 数据摄入管道
    - dedup      : 同款聚合
    - category_map : 类目映射
    - compliance  : PII 脱敏
    - db          : 数据库管理
    - models      : V1 规则版 / V2 LightGBM
"""

__version__ = "0.1.0"
