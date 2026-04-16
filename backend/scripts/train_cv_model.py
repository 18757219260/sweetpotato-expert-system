import torch
import torch.nn as nn
import torch.optim as optim
from torchvision import models, transforms
from torch.utils.data import DataLoader, Dataset
from pathlib import Path
import json
from collections import Counter
import sys
from sklearn.model_selection import train_test_split
from PIL import Image


IMAGES_DIR = Path(__file__).parent.parent / "static" / "images"
MODELS_DIR = Path(__file__).parent.parent / "models"
class_names_path = MODELS_DIR / "class_names.json"

BATCH_SIZE = 16
EPOCHS = 100
WARMUP_EPOCHS = 5  
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

def load_need_list():
    if not class_names_path.exists():
        print(f"错误：找不到 {class_names_path}")
        sys.exit(1)
    with open(class_names_path, 'r', encoding='utf-8') as f:
        need_list = json.load(f)
    return need_list


medium_transform = transforms.Compose([
    transforms.RandomResizedCrop(384, scale=(0.6, 1.0)),
    transforms.RandomHorizontalFlip(p=0.5),
    transforms.RandomVerticalFlip(p=0.4), 
    transforms.RandomRotation(35),
    transforms.ColorJitter(brightness=0.4, contrast=0.4, saturation=0.4, hue=0.15),
    transforms.RandomAffine(degrees=0, translate=(0.15, 0.15), scale=(0.9, 1.1)),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])


val_transform = transforms.Compose([
    transforms.Resize(400),               
    transforms.CenterCrop(384),
    transforms.ToTensor(),
    transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
])


class StratifiedAugmentDataset(Dataset):
    def __init__(self, root, need_list, transform, is_train=True):
        self.root = Path(root)
        self.need_list = need_list
        self.transform= transform
        self.is_train = is_train

        self.samples = []
        self.classes = []
        self.class_to_idx = {}
        self.class_counts = Counter()

        for class_dir in sorted(self.root.iterdir()):
            if not class_dir.is_dir():
                continue

            class_name = class_dir.name
            if class_name not in need_list:
                continue

            image_files = []
            for ext in ['*.jpg', '*.jpeg', '*.png', '*.JPG', '*.JPEG', '*.PNG']:
                image_files.extend(class_dir.glob(ext))

           
            image_files = sorted(image_files)

            if len(image_files) == 0:
                continue

            if class_name not in self.class_to_idx:
                self.class_to_idx[class_name] = len(self.classes)
                self.classes.append(class_name)

            class_idx = self.class_to_idx[class_name]

            valid_count = 0
            for img_path in image_files:
                try:
                    img = Image.open(img_path)
                    img.load()  
                    self.samples.append((str(img_path), class_idx))
                    self.class_counts[class_name] += 1
                    valid_count += 1
                except Exception as e:
                    print(f"   损坏的图片: {img_path.name} ({str(e)[:50]})")
                    continue

        if is_train:
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
        except Exception:
            image = Image.new('RGB', (384, 384), color='black')

        image = self.transform(image)
        return image, label


class FocalLoss(nn.Module):
    def __init__(self, gamma=2.0, alpha=None):
        super(FocalLoss, self).__init__()
        self.gamma = gamma
        self.alpha = alpha
        self.ce = nn.CrossEntropyLoss(reduction='none')

    def forward(self, inputs, targets):
        ce_loss = self.ce(inputs, targets)
        pt = torch.exp(-ce_loss)
        focal_loss = (1 - pt) ** self.gamma * ce_loss
        return focal_loss.mean()

def train_epoch(model, loader, criterion, optimizer):
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

def evaluate(model, val_loader, class_names, history):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    from sklearn.metrics import confusion_matrix

    PLOTS_DIR = MODELS_DIR / "plots"
    PLOTS_DIR.mkdir(exist_ok=True)

    epochs = range(1, len(history["train_loss"]) + 1)

    # 1. 损失曲线
    plt.figure()
    plt.plot(epochs, history["train_loss"], label="Train Loss")
    plt.plot(epochs, history["val_loss"], label="Val Loss")
    plt.xlabel("Epoch"); plt.ylabel("Loss"); plt.legend(); plt.title("Loss Curve")
    plt.savefig(PLOTS_DIR / "loss_curve.png"); plt.close()

    # 2. 准确率曲线
    plt.figure()
    plt.plot(epochs, history["train_acc"], label="Train Acc")
    plt.plot(epochs, history["val_acc"], label="Val Acc")
    plt.xlabel("Epoch"); plt.ylabel("Accuracy (%)"); plt.legend(); plt.title("Accuracy Curve")
    plt.savefig(PLOTS_DIR / "acc_curve.png"); plt.close()

    # 3. 学习率曲线
    plt.figure()
    plt.plot(epochs, history["lr"])
    plt.xlabel("Epoch"); plt.ylabel("LR"); plt.title("Learning Rate")
    plt.savefig(PLOTS_DIR / "lr_curve.png"); plt.close()

    # 4. 混淆矩阵
    model.eval()
    all_preds, all_labels = [], []
    all_images = []
    with torch.no_grad():
        for images, labels in val_loader:
            outputs = model(images.to(DEVICE))
            preds = outputs.argmax(1).cpu()
            all_preds.extend(preds.tolist())
            all_labels.extend(labels.tolist())
           
            all_images.extend(images.cpu()) 

    cm = confusion_matrix(all_labels, all_preds)
    fig, ax = plt.subplots(figsize=(max(8, len(class_names)), max(6, len(class_names))))
    im = ax.imshow(cm, cmap='Blues')
    ax.set_xticks(range(len(class_names))); ax.set_yticks(range(len(class_names)))
    ax.set_xticklabels(class_names, rotation=45, ha='right', fontsize=8)
    ax.set_yticklabels(class_names, fontsize=8)
    for i in range(len(class_names)):
        for j in range(len(class_names)):
            ax.text(j, i, cm[i, j], ha='center', va='center', fontsize=7)
    plt.colorbar(im, ax=ax); plt.title("Confusion Matrix"); plt.tight_layout()
    plt.savefig(PLOTS_DIR / "confusion_matrix.png"); plt.close()

    # 5. 每类别准确率柱状图
    per_class_acc = cm.diagonal() / cm.sum(axis=1).clip(min=1) * 100
    plt.figure(figsize=(max(8, len(class_names) * 0.6), 5))
    plt.bar(class_names, per_class_acc)
    plt.xticks(rotation=45, ha='right', fontsize=8)
    plt.ylabel("Accuracy (%)"); plt.title("Per-Class Accuracy"); plt.tight_layout()
    plt.savefig(PLOTS_DIR / "per_class_acc.png"); plt.close()

    # 6. 预测样本展示
    mean = np.array([0.485, 0.456, 0.406]); std = np.array([0.229, 0.224, 0.225])
    def denorm(t):
        img = t.permute(1,2,0).numpy() * std + mean
        return img.clip(0, 1)

    correct_idx = [i for i,(p,l) in enumerate(zip(all_preds,all_labels)) if p==l][:8]
    wrong_idx   = [i for i,(p,l) in enumerate(zip(all_preds,all_labels)) if p!=l][:8]

    for title, indices in [("correct", correct_idx), ("wrong", wrong_idx)]:
        if not indices: continue
        fig, axes = plt.subplots(2, 4, figsize=(12, 6))
        for ax, i in zip(axes.flat, indices):
            ax.imshow(denorm(all_images[i]))
            ax.set_title(f"P:{class_names[all_preds[i]]}\nT:{class_names[all_labels[i]]}", fontsize=7)
            ax.axis('off')
        for ax in axes.flat[len(indices):]: ax.axis('off')
        plt.suptitle(f"{'Correct' if title=='correct' else 'Wrong'} Predictions")
        plt.tight_layout(); plt.savefig(PLOTS_DIR / f"samples_{title}.png"); plt.close()

    print(f"图表已保存至 {PLOTS_DIR}")

def main():
    print(f"使用设备：{DEVICE}")
    need_list = load_need_list()
    print(f"需要训练 {len(need_list)} 个类别：{sorted(need_list)[:5]}...")

  
    full_dataset = StratifiedAugmentDataset(root=IMAGES_DIR, need_list=need_list, transform=medium_transform, is_train=True)
    if len(full_dataset.classes) == 0:
        print("错误：没有找到有效的类别！")
        sys.exit(1)

    all_indices = list(range(len(full_dataset)))
    all_labels = [full_dataset.samples[i][1] for i in all_indices]

 
    train_idx, val_idx = train_test_split(all_indices, test_size=0.2, random_state=42, stratify=all_labels)

    train_dataset = torch.utils.data.Subset(full_dataset, train_idx)
    val_dataset_no_aug = StratifiedAugmentDataset(root=IMAGES_DIR, need_list=need_list, transform=val_transform, is_train=False)
    val_dataset = torch.utils.data.Subset(val_dataset_no_aug, val_idx)

   
    train_labels = [all_labels[i] for i in train_idx]
    class_counts = Counter(train_labels)
    sample_weights = [1.0 / class_counts[l] for l in train_labels]
    sampler = torch.utils.data.WeightedRandomSampler(sample_weights, len(sample_weights))

   
    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, sampler=sampler, num_workers=4, pin_memory=True)
    val_loader = DataLoader(val_dataset, batch_size=BATCH_SIZE, shuffle=False, num_workers=4, pin_memory=True)

    print(f"训练集：{len(train_dataset)} 张，验证集：{len(val_dataset)} 张")
    num_classes = len(full_dataset.classes)


    model = models.resnet50(weights=models.ResNet50_Weights.IMAGENET1K_V2)
    model.fc = nn.Sequential(
        nn.Dropout(0.4),
        nn.Linear(model.fc.in_features, num_classes)
    )
    model = model.to(DEVICE)
    criterion = FocalLoss(gamma=2.0)


    for param in model.parameters():
        param.requires_grad = False
    for param in model.fc.parameters():
        param.requires_grad = True

  
    optimizer = optim.AdamW(model.fc.parameters(), lr=1e-3, weight_decay=1e-4)
    scheduler = None 

    best_val_acc = 0.0

    history = {"train_loss": [], "val_loss": [], "train_acc": [], "val_acc": [], "lr": []}

    for epoch in range(EPOCHS):
        if epoch == WARMUP_EPOCHS:
            print("\n--- 开启全局微调 ---")
            for param in model.parameters():
                param.requires_grad = True

            backbone_params = [p for n, p in model.named_parameters() if 'fc' not in n]
            fc_params = model.fc.parameters()
            
  
            optimizer = optim.AdamW([
                {'params': backbone_params, 'lr': 1e-5}, 
                {'params': fc_params, 'lr': 1e-4} 
            ], weight_decay=1e-4)

    
            scheduler = optim.lr_scheduler.CosineAnnealingWarmRestarts(optimizer, T_0=10, T_mult=2)

        # 训练和验证
        train_loss, train_acc = train_epoch(model, train_loader, criterion, optimizer)
        val_loss, val_acc = validate(model, val_loader, criterion)

   
        current_lr = optimizer.param_groups[-1]['lr']
        history["train_loss"].append(train_loss)
        history["val_loss"].append(val_loss)
        history["train_acc"].append(train_acc)
        history["val_acc"].append(val_acc)
        history["lr"].append(current_lr)

  
        if scheduler is not None:
            scheduler.step()

        print(f"Epoch {epoch+1}/{EPOCHS}:")
        print(f"  训练 - Loss: {train_loss:.4f}, Acc: {train_acc:.2f}%, LR: {current_lr:.6f}")
        print(f"  验证 - Loss: {val_loss:.4f}, Acc: {val_acc:.2f}%")

        if val_acc > best_val_acc:
            best_val_acc = val_acc
           
            torch.save(model.state_dict(), MODELS_DIR / "sweet_potato_classifier_efficientnet.pth")
            print(f"  --> 保存最佳模型: {val_acc:.2f}%")

    with open(MODELS_DIR / "class_names.json", 'w', encoding='utf-8') as f:
        json.dump(full_dataset.classes, f, ensure_ascii=False, indent=2)

    with open(MODELS_DIR / "training_history.json", 'w') as f:
        json.dump(history, f, indent=2)

    print(f"\n训练完成！最佳验证准确率：{best_val_acc:.2f}%")

    best_model_path = MODELS_DIR / "sweet_potato_classifier.pth"
    if best_model_path.exists():
        
        model.load_state_dict(torch.load(best_model_path, weights_only=True))
        
    evaluate(model, val_loader, full_dataset.classes, history)

if __name__ == "__main__":
    main()