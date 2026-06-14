"""
Validate Training Data Quality

Checks the exported training data for issues before training.
"""

import csv
import json
from pathlib import Path
import sys

# Add parent path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from training.feature_engineering import TOTAL_FEATURES_PHASE_A

def get_data_dir() -> Path:
    """Get training data directory."""
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent.parent.parent
    return project_root / "data" / "training"

def validate_csv_structure(csv_path: Path, metadata: dict) -> bool:
    """Validate CSV file structure and content."""
    print("\n[1] Validating CSV structure...")

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader)

        # Check header
        expected_cols = TOTAL_FEATURES_PHASE_A + 1  # 29 features + 1 label
        if len(header) != expected_cols:
            print(f"  [FAIL] Expected {expected_cols} columns, got {len(header)}")
            return False
        print(f"  [OK] Header has {len(header)} columns")

        # Check rows
        rows = list(reader)
        if len(rows) != metadata['total_samples']:
            print(f"  [FAIL] Expected {metadata['total_samples']} rows, got {len(rows)}")
            return False
        print(f"  [OK] CSV has {len(rows)} data rows")

        # Check for missing values
        missing_count = 0
        for i, row in enumerate(rows):
            for j, val in enumerate(row):
                if val == '' or val == 'None':
                    missing_count += 1
                    if missing_count <= 5:  # Print first 5 issues
                        print(f"  [WARN] Missing value at row {i+2}, col {j+1} ({header[j]})")

        if missing_count > 0:
            print(f"  [WARN] Found {missing_count} missing values")
        else:
            print(f"  [OK] No missing values")

    return True

def validate_feature_ranges(csv_path: Path) -> bool:
    """Check if feature values are in reasonable ranges."""
    print("\n[2] Validating feature value ranges...")

    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        header = next(reader)

        # Collect min/max for each column
        rows = list(reader)
        num_features = len(header) - 1  # Exclude label

        mins = [float('inf')] * len(header)
        maxs = [float('-inf')] * len(header)

        for row in rows:
            for i, val in enumerate(row):
                try:
                    num_val = float(val)
                    mins[i] = min(mins[i], num_val)
                    maxs[i] = max(maxs[i], num_val)
                except (ValueError, TypeError):
                    # Skip non-numeric values (expected during validation)
                    pass

        # Check for suspicious ranges
        issues = []
        for i in range(num_features):
            feat_name = header[i]

            # Check for constant features (no variance)
            if mins[i] == maxs[i]:
                issues.append(f"  [WARN] Feature '{feat_name}' is constant ({mins[i]})")

            # Check for extreme values
            if abs(mins[i]) > 1e6 or abs(maxs[i]) > 1e6:
                issues.append(f"  [WARN] Feature '{feat_name}' has extreme values: [{mins[i]:.2f}, {maxs[i]:.2f}]")

        if issues:
            for issue in issues[:10]:  # Show first 10 issues
                print(issue)
            print(f"  Found {len(issues)} potential issues")
        else:
            print(f"  [OK] All feature ranges look reasonable")

        # Print label range
        label_min = mins[-1]
        label_max = maxs[-1]
        print(f"\n  Label (llm_final_score) range: [{label_min:.2f}, {label_max:.2f}]")

    return True

def validate_query_groups(metadata: dict) -> bool:
    """Validate query group structure for GroupKFold."""
    print("\n[3] Validating query groups...")

    query_groups = metadata['query_groups']
    total_samples = sum(query_groups)

    if total_samples != metadata['total_samples']:
        print(f"  [FAIL] Query group sum ({total_samples}) != total samples ({metadata['total_samples']})")
        return False

    print(f"  [OK] Query groups sum to {total_samples} samples")
    print(f"  [OK] {len(query_groups)} queries with sizes: {query_groups}")

    # Check for very small groups (problematic for cross-validation)
    small_groups = [g for g in query_groups if g < 5]
    if small_groups:
        print(f"  [WARN] {len(small_groups)} queries have < 5 documents (may affect GroupKFold)")

    return True

def validate_metadata(metadata_path: Path) -> dict:
    """Load and validate metadata file."""
    print("\n[0] Validating metadata...")

    with open(metadata_path, 'r', encoding='utf-8') as f:
        metadata = json.load(f)

    required_keys = [
        'feature_version', 'expected_features', 'feature_names',
        'total_samples', 'total_queries', 'query_groups',
        'label_type', 'label_description'
    ]

    for key in required_keys:
        if key not in metadata:
            print(f"  [FAIL] Missing required key: {key}")
            return None

    print(f"  [OK] Metadata has all required keys")
    print(f"  Feature version: {metadata['feature_version']}")
    print(f"  Expected features: {metadata['expected_features']}")
    print(f"  Total samples: {metadata['total_samples']}")
    print(f"  Total queries: {metadata['total_queries']}")

    return metadata

def main():
    """Run all validation checks."""
    print("=" * 60)
    print("Training Data Validation")
    print("=" * 60)

    data_dir = get_data_dir()
    csv_path = data_dir / "training_data.csv"
    metadata_path = data_dir / "training_metadata.json"

    if not csv_path.exists():
        print(f"\n[FAIL] CSV file not found: {csv_path}")
        print("Run export_training_data.py first")
        return False

    if not metadata_path.exists():
        print(f"\n[FAIL] Metadata file not found: {metadata_path}")
        return False

    # Load metadata
    metadata = validate_metadata(metadata_path)
    if not metadata:
        return False

    # Run validation checks
    checks = [
        validate_csv_structure(csv_path, metadata),
        validate_feature_ranges(csv_path),
        validate_query_groups(metadata)
    ]

    # Summary
    print("\n" + "=" * 60)
    if all(checks):
        print("[PASS] All validation checks passed!")
        print("=" * 60)
        print("\nData is ready for training.")
        print("Next step: Run xgboost_trainer.py to train the model")
        return True
    else:
        print("[FAIL] Some validation checks failed")
        print("=" * 60)
        print("\nPlease fix the issues before training.")
        return False

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
