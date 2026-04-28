import pandas as pd
import json
import os

def process_operators(csv_path):
    df = pd.read_csv(csv_path)
    operators = []
    for _, row in df.iterrows():
        operators.append({
            "name": row['name'],
            "category": row['category'],
            "definition": row['definition'],
            "description": row['description']
        })
    return operators

def process_datafields(csv_path):
    df = pd.read_csv(csv_path)
    # 필요한 컬럼만 추출하여 경량화
    fields = []
    for _, row in df.iterrows():
        fields.append({
            "id": row['id'],
            "description": row['description'],
            "category": row['category_name'] if 'category_name' in row else "",
            "type": row['type']
        })
    return fields

if __name__ == "__main__":
    base_dir = "elequant-miner/data"
    
    print("Processing operators...")
    operators = process_operators("IQC_brain_operators.csv")
    with open(f"{base_dir}/operators.json", "w", encoding="utf-8") as f:
        json.dump(operators, f, indent=4, ensure_ascii=False)
    
    print("Processing datafields...")
    fields = process_datafields("IQC_brain_datafields.csv")
    with open(f"{base_dir}/datafields.json", "w", encoding="utf-8") as f:
        json.dump(fields, f, indent=4, ensure_ascii=False)
        
    print("Knowledge base created successfully in elequant-miner/data/")
