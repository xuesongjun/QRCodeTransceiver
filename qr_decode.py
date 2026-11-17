"""屏幕与视频共用的 QR 图像解码器, 不负责传输协议或任务保存."""

import zlib
from typing import List, Optional, Tuple
import cv2
import numpy as np

Detection = Tuple[str, Optional[Tuple[int, int, int, int]]]

DECODER_NAME = None

# 1. 尝试 pyzbar (需额外安装: pip install pyzbar，Linux 还需 libzbar0)
try:
    import pyzbar.pyzbar as pyzbar

    def qrdecode(image: np.ndarray, multi: bool = False) -> List[Detection]:
        """使用 pyzbar 解码 QR 码，返回 Detection 列表."""
        detections: List[Detection] = []
        for obj in pyzbar.decode(image):
            rect = obj.rect
            detections.append(
                (obj.data.decode("utf-8"), (rect.left, rect.top, rect.width, rect.height))
            )
        return detections

    DECODER_NAME = "pyzbar"
except (ImportError, OSError):
    pass

# 2. 回退到 OpenCV 内置 QR 检测器
if DECODER_NAME is None:
    qrDecoder = cv2.QRCodeDetector()

    def _rect_from_points(points: Optional[np.ndarray]) -> Optional[Tuple[int, int, int, int]]:
        """将 OpenCV 返回的角点数组转换为 (left, top, width, height) 矩形."""
        if points is None or points.size == 0:
            return None
        pts = points.reshape(-1, 2)
        xs = pts[:, 0]
        ys = pts[:, 1]
        left = int(xs.min())
        top = int(ys.min())
        right = int(xs.max())
        bottom = int(ys.max())
        return (left, top, right - left, bottom - top)

    def qrdecode(image: np.ndarray, multi: bool = False) -> List[Detection]:
        """使用 OpenCV 解码 QR 码，multi=True 时尝试多码检测."""
        detections: List[Detection] = []
        if multi:
            try:
                retval, decoded_infos, points, _ = qrDecoder.detectAndDecodeMulti(image)
            except cv2.error:
                retval = False
            if retval:
                for data, pts in zip(decoded_infos, points):
                    if data:
                        detections.append((data, _rect_from_points(pts)))
                return detections

        try:
            data, points, _ = qrDecoder.detectAndDecode(image)
        except cv2.error:
            data = ""
            points = None
        if data:
            detections.append((data, _rect_from_points(points)))
        return detections

    DECODER_NAME = "opencv"


def decode_with_fallback(image: np.ndarray, multi: bool = False) -> List[Detection]:
    """
    先尝试正常图像解码，失败后对图像取反再试一次.

    某些屏幕上 QR 码颜色反转 (白底黑码变黑底白码) 时，
    取反后可提高识别率.
    """
    detections = qrdecode(image, multi=multi)
    if detections:
        return detections
    if not multi:
        detections = qrdecode(image, multi=True)
        if detections:
            return detections
    inverted = cv2.bitwise_not(image)
    detections = qrdecode(inverted, multi=multi)
    if detections or multi:
        return detections
    return qrdecode(inverted, multi=True)


def frame_signature(image: np.ndarray) -> Tuple[Tuple[int, ...], int]:
    return image.shape, zlib.crc32(image.tobytes())
