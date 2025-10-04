class DeEarthError(Exception):
    """所有自定义异常的基类。"""
    pass

class DownloaderError(DeEarthError):
    """下载过程中发生的错误。"""
    pass

class PackInstallError(DeEarthError):
    """整合包安装或解压过程中发生的错误。"""
    pass

class ModIdentificationError(DeEarthError):
    """模组识别失败的错误。"""
    pass

class PlatformError(DeEarthError):
    """平台 API 或逻辑错误。"""
    pass