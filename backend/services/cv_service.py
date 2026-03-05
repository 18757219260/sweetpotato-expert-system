"""
services/cv_service.py - 本地 CV 模型推理服务

功能：
1. 加载训练好的 PyTorch 模型（ResNet18）
2. 对上传图片进行预处理（resize, normalize）
3. 返回分类结果（类别名 + 置信度）
4. 支持模型热加载（首次调用时加载，避免启动延迟）

依赖：
- torch, torchvision
- PIL
"""

import os
import json
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from dotenv import load_dotenv

load_dotenv()

# ── 配置 ─────────────────────────────────────────────────────────────────────
MODEL_PATH = Path(__file__).parent.parent / "models" / "sweet_potato_classifier.pth"
CLASS_NAMES_PATH = Path(__file__).parent.parent / "models" / "class_names.json"
KB_PATH = Path(__file__).parent.parent / "data" / "knowledge_base.json"
DEVICE_TYPE = "cuda"  # 使用 GPU 加速，如无 GPU 可改为 "cpu"

# 全局模型实例（懒加载）
_model: Optional[object] = None
_class_names: Optional[List[str]] = None
_image_id_to_name: Optional[Dict[str, str]] = None


def _load_image_id_mapping():
    """加载 image_id 到中文名称的映射"""
    global _image_id_to_name

    if _image_id_to_name is not None:
        return _image_id_to_name

    _image_id_to_name = {}

    if KB_PATH.exists():
        with open(KB_PATH, 'r', encoding='utf-8') as f:
            records = json.load(f)
            for record in records:
                image_id = record.get('image_id')
                name = record.get('name')
                if image_id and name:
                    _image_id_to_name[image_id] = name

    return _image_id_to_name


def get_chinese_name(image_id: str) -> str:
    """将 image_id 转换为中文名称"""
    mapping = _load_image_id_mapping()
    return mapping.get(image_id, image_id)  # 如果找不到，返回原 ID



def _load_model():
    """懒加载模型（首次调用时初始化）"""
    global _model, _class_names

    if _model is not None:
        return  # 已加载

    try:
        import torch
        import torch.nn as nn
        from torchvision import models

        device = torch.device(DEVICE_TYPE)

        # 1. 加载类别名称（从训练时保存的 JSON）
        if not CLASS_NAMES_PATH.exists():
            raise FileNotFoundError(
                f"类别映射文件不存在：{CLASS_NAMES_PATH}\n"
                "请先运行训练脚本生成模型和类别映射"
            )

        with open(CLASS_NAMES_PATH, 'r', encoding='utf-8') as f:
            _class_names = json.load(f)

        num_classes = len(_class_names)

        # 2. 构建 ResNet18 模型架构（与训练时保持一致）
        model = models.resnet18(pretrained=False)
        model.fc = nn.Linear(model.fc.in_features, num_classes)

        # 3. 加载权重
        if not MODEL_PATH.exists():
            raise FileNotFoundError(
                f"CV 模型文件不存在：{MODEL_PATH}\n"
                "请先运行训练脚本生成模型权重"
            )

        state_dict = torch.load(MODEL_PATH, map_location=device)
        model.load_state_dict(state_dict)
        model.to(device)
        model.eval()

        _model = model
        print(f"[CV] 模型加载成功：{MODEL_PATH} (设备: {device}, 类别数: {num_classes})")

    except ImportError as e:
        raise ImportError(
            "PyTorch 未安装，请运行: pip install torch torchvision\n"
            f"原始错误: {e}"
        )


def _preprocess_image(image_path: str):
    """图片预处理（与训练时保持一致）"""
    try:
        import torch
        from torchvision import transforms
        from PIL import Image

        transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

        image = Image.open(image_path).convert("RGB")
        return transform(image).unsqueeze(0)  # 添加 batch 维度

    except ImportError as e:
        raise ImportError(f"缺少依赖库: {e}")


def classify_image(image_path: str, top_k: int = 3) -> List[Tuple[str, float]]:
    """
    对图片进行分类推理

    Args:
        image_path: 图片文件路径
        top_k: 返回前 k 个最高置信度的结果

    Returns:
        [(类别名, 置信度), ...] 列表，按置信度降序排列
    """
    _load_model()  # 懒加载

    import torch

    device = torch.device(DEVICE_TYPE)

    # 1. 预处理
    input_tensor = _preprocess_image(image_path).to(device)

    # 2. 推理
    with torch.no_grad():
        outputs = _model(input_tensor)
        probabilities = torch.nn.functional.softmax(outputs, dim=1)[0]

    # 3. 获取 top-k 结果
    top_probs, top_indices = torch.topk(probabilities, top_k)

    results = [
        (_class_names[idx.item()], prob.item())
        for idx, prob in zip(top_indices, top_probs)
    ]

    return results


def format_classification_result(results: List[Tuple[str, float]]) -> str:
    """
    将分类结果格式化为自然语言文本，供 LLM 使用

    Example output:
    "根据图片识别，最可能是：烟薯25（概率 92.3%）。
     其他可能：黑斑病（概率 5.1%）、疮痂病（概率 1.8%）。"
    """
    if not results:
        return "图片识别失败，无法确定病害类型。"

    top_class, top_conf = results[0]
    top_name = get_chinese_name(top_class)

    # 使用中文名称和"概率"
    main_text = f"根据图片识别，最可能是：{top_name}（概率 {top_conf*100:.1f}%）。"

    if len(results) > 1:
        others = "、".join([
            f"{get_chinese_name(cls)}（{conf*100:.1f}%）" for cls, conf in results[1:]
        ])
        main_text += f"\n其他可能：{others}。"

    return main_text
