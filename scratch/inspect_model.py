import joblib
import numpy as np

data = joblib.load("data/ml_model.joblib")
print("Model keys:", data.keys())
print("Features in model:", data.get("features"))
model = data["model"]
print("Model type:", type(model))

# If XGBoost or sklearn, let's see feature importances
if hasattr(model, "feature_importances_"):
    for f, imp in zip(data.get("features", []), model.feature_importances_):
        print(f"Feature: {f:<22} Importance: {imp:.4f}")
