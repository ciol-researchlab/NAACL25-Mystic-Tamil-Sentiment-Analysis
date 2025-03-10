# Tabjular Data Analysis
import numpy as np
import pandas as pd

# Visualization
import matplotlib.pyplot as plt
import seaborn as sns

# Utility
import time
import warnings
warnings.filterwarnings('ignore')

"""# 4. Load the dataset"""

train_df = pd.read_csv("/content/Tam-SA-train.csv")
train_df.head(3)

val_df = pd.read_csv("/content/Tam-SA-val.csv")
val_df.head(3)

test_df = pd.read_csv("/content/Tam-SA-test-without-labels.csv")
test_df.head(3)

TEXT_VAR = "Text"
LABEL_VAR = "Label"

"""Labels are not numerical. Let's make them numerical."""

# Map text labels to numerical values
label_mapping = {label: idx for idx, label in enumerate(train_df[LABEL_VAR].unique())}
train_df[LABEL_VAR] = train_df[LABEL_VAR].map(label_mapping)  # Change as necessary
val_df[LABEL_VAR] = val_df[LABEL_VAR].map(label_mapping)  # Change as necessary

"""# Modeling

## Load Things
"""

import os
import torch
import torch.nn as nn
import pandas as pd
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModel, AutoTokenizer, AutoProcessor
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
from tqdm import tqdm

# Set device
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

seed = 42
torch.manual_seed(seed)

# Hyperparameters
model_name = "l3cube-pune/indic-sentence-similarity-sbert"
batch_size = 32
max_length = 1024

# Load Tokenizer and Model
text_tokenizer = AutoTokenizer.from_pretrained(model_name)
text_model = AutoModel.from_pretrained(model_name).to(device)

class TextDataset(Dataset):
    def __init__(self, df, tokenizer):
        self.df = df
        self.tokenizer = tokenizer

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        transcription = self.df.iloc[idx][TEXT_VAR]
        transcription = transcription if isinstance(transcription, str) else ""
        inputs = self.tokenizer(
            transcription, padding="max_length", truncation=True, max_length=max_length, return_tensors="pt"
        )
        return inputs, self.df.iloc[idx][LABEL_VAR]

"""## Collect Embeddings"""

def extract_text_embeddings(df, save_path, model, tokenizer):
    if os.path.exists(save_path):
        print(f"Embeddings already exist at {save_path}")
        return torch.load(save_path)

    embeddings = {}
    model.eval()
    with torch.no_grad():
        for idx, row in tqdm(df.iterrows(), desc="Extracting text embeddings", total=len(df)):
            transcription = row[TEXT_VAR]
            transcription = transcription if isinstance(transcription, str) else ""

            # Tokenize the text
            inputs = tokenizer(
                transcription, padding="max_length", truncation=True, max_length=128, return_tensors="pt"
            )
            inputs = {key: value.to(device) for key, value in inputs.items()}  # Move inputs to GPU/CPU

            # Extract embeddings
            outputs = model(**inputs)
            cls_embedding = outputs.last_hidden_state[:, 0, :]  # [CLS] token embeddings
            embeddings[idx] = cls_embedding.cpu()  # Use the index as the key

    torch.save(embeddings, save_path)
    return embeddings

train_text_embeddings = extract_text_embeddings(
    train_df, "train_text_embeddings.pt", text_model, text_tokenizer
)
val_text_embeddings = extract_text_embeddings(
    val_df, "val_text_embeddings.pt", text_model, text_tokenizer
)
test_text_embeddings = extract_text_embeddings(
    test_df, "test_text_embeddings.pt", text_model, text_tokenizer
)

"""## Load Embeddings"""

def load_embeddings(embedding_path):
    if os.path.exists(embedding_path):
        print(f"Loading embeddings from {embedding_path}")
        return torch.load(embedding_path)
    else:
        raise FileNotFoundError(f"Embeddings file not found at {embedding_path}")

train_text_embeddings = load_embeddings("/content/train_text_embeddings.pt")
val_text_embeddings = load_embeddings("/content/val_text_embeddings.pt")
test_text_embeddings = load_embeddings("/content/test_text_embeddings.pt")

"""## Modeling"""

import torch.optim as optim
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from torch.utils.data import DataLoader, TensorDataset

def prepare_text_embeddings(text_embeddings, df, LABEL_VAR, has_labels=True):
    combined_embeddings = []
    labels = [] if has_labels else None

    for idx, row in df.iterrows():
        # Ensure the index exists in the text embeddings
        if idx in text_embeddings:
            text_embedding = text_embeddings[idx].squeeze()  # Squeeze to remove unnecessary dimensions

            # Add the text embedding to the list
            combined_embeddings.append(text_embedding)

            if has_labels:
                labels.append(row[LABEL_VAR])  # Get the label from the DataFrame

    if has_labels:
        return torch.stack(combined_embeddings), torch.tensor(labels)
    else:
        return torch.stack(combined_embeddings)

X_train, y_train = prepare_text_embeddings(train_text_embeddings, train_df, LABEL_VAR)
X_val, y_val = prepare_text_embeddings(val_text_embeddings, val_df, LABEL_VAR)
X_test = prepare_text_embeddings(test_text_embeddings, test_df, LABEL_VAR, has_labels=False)

print(f"Training data shape: {X_train.shape}, Labels: {y_train.shape}")
print(f"Validation data shape: {X_val.shape}, Labels: {y_val.shape}")
print(f"Test data shape: {X_test.shape}")

# Define the MLP model
class MLPModel(nn.Module):
    def __init__(self, input_dim, hidden_dim, output_dim, dropout_p=0.5):
        """
        Initialize the MLP model.
        Args:
            input_dim (int): Dimension of the input features.
            hidden_dim (list of int): List of dimensions for hidden layers.
            output_dim (int): Dimension of the output layer.
            dropout_p (float): Dropout probability.
        """
        super(MLPModel, self).__init__()
        self.fc1 = nn.Linear(input_dim, hidden_dim[0])
        self.relu = nn.ReLU()
        self.dropout1 = nn.Dropout(p=dropout_p)
        self.fc2 = nn.Linear(hidden_dim[0], hidden_dim[1])
        self.dropout2 = nn.Dropout(p=dropout_p)
        self.fc3 = nn.Linear(hidden_dim[1], output_dim)
        self.softmax = nn.Softmax(dim=1)

    def forward(self, x):
        x = self.relu(self.fc1(x))
        x = self.dropout1(x)
        x = self.relu(self.fc2(x))
        x = self.dropout2(x)
        x = self.fc3(x)
        return x

# Hyperparameters
input_dim = X_train.shape[1]
num_classes = len(train_df[LABEL_VAR].unique())
hidden_dim = [1024, 512]
output_dim = num_classes
batch_size = 32
num_epochs = 50
learning_rate = 0.001
dropout_p = 0.3

seed = 42
torch.manual_seed(seed)

# Prepare the data loaders
train_dataset = TensorDataset(X_train, y_train)
val_dataset = TensorDataset(X_val, y_val)
test_dataset = TensorDataset(X_test)

train_loader = DataLoader(train_dataset, batch_size, shuffle=True)
val_loader = DataLoader(val_dataset, batch_size)
test_loader = DataLoader(test_dataset, batch_size)

# Initialize model, loss function, and optimizer
model = MLPModel(input_dim, hidden_dim, output_dim, dropout_p).to(device)
criterion = nn.CrossEntropyLoss()
optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)

# Function to calculate metrics
def calculate_metrics(preds, labels):
    accuracy = accuracy_score(labels, preds)
    precision, recall, f1, _ = precision_recall_fscore_support(labels, preds, average="macro")
    return accuracy, precision, recall, f1

"""## Train and Val"""

# Train and save best model
def train_and_save_best_model(model, train_loader, val_loader, criterion, optimizer, num_epochs, save_dir):
    best_f1 = -float('inf')
    best_model_path = None

    for epoch in range(num_epochs):
        model.train()
        train_loss = 0
        all_train_preds, all_train_labels = [], []

        for inputs, labels in train_loader:
            inputs, labels = inputs.to(device), labels.to(device)

            optimizer.zero_grad()
            outputs = model(inputs).squeeze()

            # Compute loss and backpropagate
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()

            train_loss += loss.item()

            _, preds = torch.max(outputs, dim=1)
            all_train_preds.extend(preds.cpu().tolist())
            all_train_labels.extend(labels.cpu().tolist())

        # Calculate training metrics
        train_accuracy, train_precision, train_recall, train_f1 = calculate_metrics(all_train_preds, all_train_labels)

        # Validation phase
        model.eval()
        val_loss = 0
        all_val_preds, all_val_labels = [], []

        with torch.no_grad():
            for inputs, labels in val_loader:
                inputs, labels = inputs.to(device), labels.to(device)
                outputs = model(inputs).squeeze()

                loss = criterion(outputs, labels)
                val_loss += loss.item()

                _, preds = torch.max(outputs, dim=1)
                all_val_preds.extend(preds.cpu().tolist())
                all_val_labels.extend(labels.cpu().tolist())

        # Calculate validation metrics
        val_accuracy, val_precision, val_recall, val_f1 = calculate_metrics(all_val_preds, all_val_labels)

        print(f"Epoch {epoch+1}/{num_epochs}: Train Loss: {train_loss/len(train_loader):.4f}, "
              f"Train Acc: {train_accuracy:.4f}, Prec: {train_precision:.4f}, Rec: {train_recall:.4f}, F1: {train_f1:.4f} | "
              f"Val Loss: {val_loss/len(val_loader):.4f}, Val Acc: {val_accuracy:.4f}, Prec: {val_precision:.4f}, "
              f"Rec: {val_recall:.4f}, F1: {val_f1:.4f}")

        # Save the model if it has the best F1 score on validation
        if val_f1 > best_f1:
            best_f1 = val_f1
            best_model_path = f"{save_dir}/best_model_epoch_{epoch + 1}_f1_{val_f1:.4f}.pth"
            torch.save(model.state_dict(), best_model_path)
            print(f"Best model saved with F1: {val_f1:.4f} at epoch {epoch + 1}")

    return best_model_path

# Set the directory where the best model will be saved
save_dir = "./models"
os.makedirs(save_dir, exist_ok=True)

# Train the model and save the best model
best_model_path = train_and_save_best_model(
    model=model,
    train_loader=train_loader,
    val_loader=val_loader,
    criterion=criterion,
    optimizer=optimizer,
    num_epochs=num_epochs,
    save_dir=save_dir
)

print(f"Best model saved at: {best_model_path}")

"""## Test"""

def predict_and_generate_submission(test_loader, best_model_path, submission_file_path):
    # Load the best model with weights_only=True to avoid security warnings
    model = MLPModel(input_dim, hidden_dim, output_dim, dropout_p).to(device)
    model.load_state_dict(torch.load(best_model_path, weights_only=True))
    model.eval()  # Set the model to evaluation mode

    test_predictions = []
    with torch.no_grad():
        for inputs in test_loader:
            # Ensure inputs are converted to a tensor and stacked into a batch if necessary
            if isinstance(inputs, list):
                # Convert each item to tensor using .detach() to avoid the user warning
                inputs = [i.clone().detach().to(device) if isinstance(i, torch.Tensor) else torch.tensor(i).to(device) for i in inputs]
                inputs = torch.stack(inputs)  # Stack them into a batch tensor
            else:
                inputs = inputs.to(device)  # If inputs is already a tensor, move it to device

            outputs = model(inputs).squeeze()

            # Predict binary labels
            _, preds = torch.max(outputs, dim=1)
            test_predictions.extend(preds.tolist())

    # Prepare the submission DataFrame
    submission_df = pd.DataFrame({
        TEXT_VAR: [i for i in test_df[TEXT_VAR]],
        'predictions': test_predictions
    })

    # Save the predictions to a CSV file
    submission_df.to_csv(submission_file_path, index=False)
    print(f"Submission file saved to {submission_file_path}")

    return submission_df

submission_file_path = "submission.csv"
submission_df = predict_and_generate_submission(test_loader=test_loader, best_model_path=best_model_path, submission_file_path=submission_file_path)

submission_df.head()

"""If you use it, cite:

*Azmine Toushik Wasi. (2024). CIOL Presnts Winer ML BootCamp. https://github.com/ciol-researchlab/CIOL-Winter-ML-Bootcamp*

```
@misc{wasi2024CIOL-WMLB,
      title={CIOL Presnts Winer ML BootCamp},
      author={Azmine Toushik Wasi},
      year={2024},
      url={https://github.com/ciol-researchlab/CIOL-Winter-ML-Bootcamp},
}```
"""