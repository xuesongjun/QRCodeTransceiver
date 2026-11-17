"""共用二维码渲染, 保持锐利边缘和静区."""

import qrcode
from qrcode.exceptions import DataOverflowError
from PIL import Image

ERROR_CORRECTION_LEVELS = {
    "L": qrcode.constants.ERROR_CORRECT_L,  # ~7% 纠错
    "M": qrcode.constants.ERROR_CORRECT_M,  # ~15% 纠错 (默认)
    "Q": qrcode.constants.ERROR_CORRECT_Q,  # ~25% 纠错
    "H": qrcode.constants.ERROR_CORRECT_H,  # ~30% 纠错
}


def build_qr(
    droplet: str,
    size: int = 512,
    border: int = 4,
    error_correction: int = qrcode.constants.ERROR_CORRECT_M,
) -> Image.Image:
    """
    将单个 droplet 字符串编码为 QR 码图片.

    参数
    ----
    droplet          : str   droplet 序列化字符串 (seed|num_chunks|padding|b64)
    size             : int   输出图片的像素边长 (正方形) , 0 表示不缩放
    border           : int   QR 静区宽度 (模块数)
    error_correction : int   纠错等级常量

    返回
    ----
    PIL.Image.Image  RGB 模式的 QR 码图片

    异常
    ----
    ValueError  当 chunk_size 过大导致单个 droplet 超出 QR 容量时抛出
    """
    qr = qrcode.QRCode(
        version=None,          # 自动选择最小版本
        error_correction=error_correction,
        box_size=10,
        border=border,
    )
    qr.add_data(droplet)
    try:
        qr.make(fit=True)
    except DataOverflowError as exc:
        raise ValueError(
            "当前 QR 参数无法容纳单个 droplet, 请降低 --chunk-size "
            "或降低 --error-correction."
        ) from exc
    base_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    if size:
        # 使用 NEAREST 插值保持 QR 码锐利边缘
        base_img = base_img.resize((size, size), Image.Resampling.NEAREST)
    return base_img

