#!/usr/bin/env python3
"""
甘薯病害 CV 分类模型训练脚本
使用 ResNet18 + 分层数据增强策略
"""

import torch
import torch.nn as nn
import torch.optim as optim
from torchvision import models, transforms, datasets
from torch.utils.data import DataLoader, Dataset
from pathlib import Path
import json
from collections import Counter
import sys
   
from PIL import Image
# 配置参数
IMAGES_DIR = Path(__file__).parent.parent / "static" / "images"
MODELS_DIR = Path(__file__).parent.parent / "models"
NONEED_TXT = Path(__file__).parent.parent.parent / "noneed.txt"
BATCH_SIZE = 32
EPOCHS = 100  # 增加到 100
LEARNING_RATE = 0.0001  # 降低学习率
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

def load_exclude_list():

    with open(NONEED_TXT, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    exclude_ids = []
    for line in lines:  
        exclude_ids.extend(line.strip().split())

    return set(exclude_ids)

# 定义三种数据增强策略（增强版）
standard_transform = transforms.Compose([
    transforms.RandomResizedCrop(224, scale=(0.7, 1.0)),  # 更激进的裁剪
    transforms.RandomHorizontalFlip(p=0.5),
    transforms.RandomRotation(20),  # 增加旋转角度
    transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.3, hue=0.1),  # 增强颜色抖动
    transforms.RandomAffine(degrees=0, translate=(0.1, 0.1)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])

medium_transform = transforms.Compose([
    transforms.RandomResizedCrop(224, scale=(0.6, 1.0)),
    transforms.RandomHorizontalFlip(p=0.5),
    transforms.RandomVerticalFlip(p=0.4),  # 增加垂直翻转概率
    transforms.RandomRotation(35),
    transforms.ColorJitter(brightness=0.4, contrast=0.4, saturation=0.4, hue=0.15),
    transforms.RandomAffine(degrees=0, translate=(0.15, 0.15), scale=(0.9, 1.1)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])

aggressive_transform = transforms.Compose([
    transforms.RandomResizedCrop(224, scale=(0.5, 1.0)),  # 更激进的裁剪
    transforms.RandomHorizontalFlip(p=0.5),
    transforms.RandomVerticalFlip(p=0.5),
    transforms.RandomRotation(50),  # 增加旋转角度
    transforms.ColorJitter(brightness=0.5, contrast=0.5, saturation=0.5, hue=0.2),
    transforms.RandomAffine(degrees=0, translate=(0.2, 0.2), scale=(0.8, 1.2)),
    transforms.RandomPerspective(distortion_scale=0.3, p=0.5),  # 增加透视变换
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])

val_transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])


class StratifiedAugmentDataset(Dataset):
    """支持分层数据增强的数据集"""

    def __init__(self, root, exclude_list, transform_dict, is_train=True):
        self.root = Path(root)
        self.exclude_list = exclude_list
        self.transform_dict = transform_dict
        self.is_train = is_train

        # 扫描目录，构建样本列表
        self.samples = []
        self.classes = []
        self.class_to_idx = {}
        self.class_counts = Counter()

        for class_dir in sorted(self.root.iterdir()):
            if not class_dir.is_dir():
                continue

            class_name = class_dir.name
            if class_name in exclude_list:
                continue

            # 先收集该类别的所有图片（只支持 jpg 和 png）
            image_files = []
            for ext in ['*.jpg', '*.jpeg', '*.png', '*.JPG', '*.JPEG', '*.PNG']:
                image_files.extend(class_dir.glob(ext))

            # 只有当目录中有图片时才添加类别
            if len(image_files) == 0:
                continue

            # 添加类别
            if class_name not in self.class_to_idx:
                self.class_to_idx[class_name] = len(self.classes)
                self.classes.append(class_name)

            class_idx = self.class_to_idx[class_name]

            # 添加该类别的所有图片（验证图片是否可读）
            valid_count = 0
            for img_path in image_files:
                try:
                    # 完全加载图片以验证其有效性（不只是 verify）
                    img = Image.open(img_path)
                    img.load()  # 强制加载完整图片数据
                    img.close()

                    self.samples.append((str(img_path), class_idx))
                    self.class_counts[class_name] += 1
                    valid_count += 1
                except Exception as e:
                    print(f"  ⚠️  跳过损坏的图片: {img_path.name} ({str(e)[:50]})")
                    continue

            if valid_count == 0:
                # 如果该类别没有有效图片，移除该类别
                self.classes.remove(class_name)
                del self.class_to_idx[class_name]

        print(f"加载数据集：{len(self.classes)} 个类别，{len(self.samples)} 张图片")
        print(f"类别样本分布：")
        for class_name in sorted(self.class_counts.keys()):
            count = self.class_counts[class_name]
            print(f"  {class_name}: {count} 张")

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        img_path, label = self.samples[idx]

        try:
            image = Image.open(img_path).convert('RGB')
        except Exception as e:
            # 如果图片损坏，返回一个黑色图片作为占位符
            print(f"⚠️  运行时图片加载失败: {img_path} ({str(e)[:50]})")
            image = Image.new('RGB', (224, 224), color='black')

        if self.is_train:
            # 根据类别样本数选择增强策略
            class_name = self.classes[label]
            count = self.class_counts[class_name]

            if count >= 20:
                transform = self.transform_dict['standard']
            elif count >= 10:
                transform = self.transform_dict['medium']
            else:
                transform = self.transform_dict['aggressive']
        else:
            transform = self.transform_dict['val']

        image = transform(image)
        return image, label


def compute_class_weights(dataset):
    """计算类别权重以处理样本不均衡"""
    class_counts = [dataset.class_counts[class_name] for class_name in dataset.classes]
    total = sum(class_counts)
    weights = [total / (len(class_counts) * count) for count in class_counts]
    return torch.FloatTensor(weights).to(DEVICE)


def train_epoch(model, loader, criterion, optimizer):
    """训练一个 epoch"""
    model.train()
    running_loss = 0.0
    correct = 0
    total = 0

    for images, labels in loader:
        images, labels = images.to(DEVICE), labels.to(DEVICE)

        optimizer.zero_grad()
        outputs = model(images)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()

        running_loss += loss.item()
        _, predicted = outputs.max(1)
        total += labels.size(0)
        correct += predicted.eq(labels).sum().item()

    return running_loss / len(loader), 100.0 * correct / total


def validate(model, loader, criterion):
    """验证模型"""
    model.eval()
    running_loss = 0.0
    correct = 0
    total = 0

    with torch.no_grad():
        for images, labels in loader:
            images, labels = images.to(DEVICE), labels.to(DEVICE)
            outputs = model(images)
            loss = criterion(outputs, labels)

            running_loss += loss.item()
            _, predicted = outputs.max(1)
            total += labels.size(0)
            correct += predicted.eq(labels).sum().item()

    return running_loss / len(loader), 100.0 * correct / total


def main():
    print(f"使用设备：{DEVICE}")

    # 1. 加载排除列表
    exclude_list = load_exclude_list()
    print(f"排除 {len(exclude_list)} 个类别：{sorted(exclude_list)[:5]}...")

    # 2. 准备数据增强字典
    transform_dict = {
        'standard': standard_transform,
        'medium': medium_transform,
        'aggressive': aggressive_transform,
        'val': val_transform
    }

    # 3. 加载完整数据集
    full_dataset = StratifiedAugmentDataset(
        root=IMAGES_DIR,
        exclude_list=exclude_list,
        transform_dict=transform_dict,
        is_train=True
    )

    if len(full_dataset.classes) == 0:
        print("错误：没有找到有效的类别！")
        sys.exit(1)

    # 4. 划分训练集和验证集 (80/20)
    train_size = int(0.8 * len(full_dataset))
    val_size = len(full_dataset) - train_size
    train_indices, val_indices = torch.utils.data.random_split(
        range(len(full_dataset)), [train_size, val_size]
    )

    # 创建训练集和验证集的子集
    train_dataset = torch.utils.data.Subset(full_dataset, train_indices.indices)

    # 验证集使用不同的 transform（不增强）
    val_dataset_no_aug = StratifiedAugmentDataset(
        root=IMAGES_DIR,
        exclude_list=exclude_list,
        transform_dict=transform_dict,
        is_train=False
    )
    val_dataset = torch.utils.data.Subset(val_dataset_no_aug, val_indices.indices)

    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True, num_workers=4)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=4)

    print(f"训练集：{len(train_dataset)} 张，验证集：{len(val_dataset)} 张")

    # 5. 构建 ResNet18 模型
    num_classes = len(full_dataset.classes)
    print(f"类别数：{num_classes}")

    model = models.resnet18(pretrained=True)
    model.fc = nn.Linear(model.fc.in_features, num_classes)
    model = model.to(DEVICE)

    # 6. 计算类别权重
    class_weights = compute_class_weights(full_dataset)
    criterion = nn.CrossEntropyLoss(weight=class_weights)
    optimizer = optim.Adam(model.parameters(), lr=LEARNING_RATE)

    # 添加学习率调度器（当验证准确率不再提升时降低学习率）
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode='max', factor=0.5, patience=5
    )

    # 7. 训练循环
    best_val_acc = 0.0
    patience = 15  # 增加 patience
    patience_counter = 0

    for epoch in range(EPOCHS):
        train_loss, train_acc = train_epoch(model, train_loader, criterion, optimizer)
        val_loss, val_acc = validate(model, val_loader, criterion)

        # 更新学习率调度器
        scheduler.step(val_acc)

        print(f"Epoch {epoch+1}/{EPOCHS}:")
        print(f"  训练 - Loss: {train_loss:.4f}, Acc: {train_acc:.2f}%")
        print(f"  验证 - Loss: {val_loss:.4f}, Acc: {val_acc:.2f}%")
        print(f"  当前学习率: {optimizer.param_groups[0]['lr']:.6f}")

        # 保存最佳模型
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            patience_counter = 0

            torch.save(model.state_dict(), MODELS_DIR / "sweet_potato_classifier.pth")
            print(f"  ✓ 保存最佳模型（验证准确率: {val_acc:.2f}%）")
        else:
            patience_counter += 1

        # Early stopping
        if patience_counter >= patience:
            print(f"Early stopping at epoch {epoch+1}")
            break

    # 8. 保存类别名称映射
    with open(MODELS_DIR / "class_names.json", 'w', encoding='utf-8') as f:
        json.dump(full_dataset.classes, f, ensure_ascii=False, indent=2)

    print(f"\n训练完成！")
    print(f"最佳验证准确率：{best_val_acc:.2f}%")
    print(f"模型保存至：{MODELS_DIR / 'sweet_potato_classifier.pth'}")
    print(f"类别映射保存至：{MODELS_DIR / 'class_names.json'}")


if __name__ == "__main__":
    main()

