class Solution:
    def twoSum(self, nums: list[int], target: int) -> list[int]:
        num_to_index = {}
        for index, num in enumerate(nums):
            complement = target - num
            if complement in num_to_index:
                return [num_to_index[complement], index]
            num_to_index[num] = index
        return []


# ============================================================================
# COMPLEX DEPENDENCY CHAIN GRAPH - Data Processing Pipeline
# ============================================================================
# This creates a multi-layer dependency structure where functions depend on
# multiple other functions, forming a chain graph for analysis.
# ============================================================================

# ============================================================================
# Layer 1: Core Utility Functions (Foundation)
# ============================================================================

def validate_input(data: list) -> bool:
    """Validate input data structure and types"""
    if not isinstance(data, list):
        return False
    return all(isinstance(x, (int, float)) for x in data)


def check_bounds(data: list, min_val: int = -1000, max_val: int = 1000) -> bool:
    """Check if all values are within acceptable bounds"""
    return all(min_val <= x <= max_val for x in data)


def normalize_data(data: list) -> list:
    """Normalize data to 0-1 range"""
    if not data:
        return []
    min_val = min(data)
    max_val = max(data)
    if min_val == max_val:
        return [0.5] * len(data)
    return [(x - min_val) / (max_val - min_val) for x in data]


# ============================================================================
# Layer 2: Data Preprocessing (depends on Layer 1)
# ============================================================================

def clean_outliers(data: list, threshold: float = 2.0) -> list:
    """Remove statistical outliers using z-score
    
    Dependencies: validate_input
    """
    if not validate_input(data):
        raise ValueError("Invalid input data")
    
    if len(data) < 2:
        return data
    
    mean = sum(data) / len(data)
    variance = sum((x - mean) ** 2 for x in data) / len(data)
    std_dev = variance ** 0.5
    
    if std_dev == 0:
        return data
    
    return [x for x in data if abs((x - mean) / std_dev) <= threshold]


def preprocess_pipeline(raw_data: list) -> list:
    """Main preprocessing pipeline combining multiple validation steps
    
    Dependencies: validate_input, check_bounds, clean_outliers
    """
    if not validate_input(raw_data):
        raise ValueError("Input validation failed")
    
    if not check_bounds(raw_data):
        raise ValueError("Bounds check failed")
    
    cleaned = clean_outliers(raw_data)
    return cleaned


# ============================================================================
# Layer 3: Feature Extraction (depends on Layer 2)
# ============================================================================

def calculate_mean(data: list) -> float:
    """Calculate arithmetic mean"""
    return sum(data) / len(data) if data else 0


def calculate_std_dev(data: list) -> float:
    """Calculate standard deviation"""
    if len(data) < 2:
        return 0
    mean = calculate_mean(data)
    variance = sum((x - mean) ** 2 for x in data) / len(data)
    return variance ** 0.5


def calculate_percentiles(data: list, percentiles: list = [25, 50, 75]) -> dict:
    """Calculate percentile values"""
    if not data:
        return {}
    sorted_data = sorted(data)
    result = {}
    for p in percentiles:
        idx = int(len(sorted_data) * p / 100)
        result[f"p{p}"] = sorted_data[min(idx, len(sorted_data) - 1)]
    return result


def extract_features(preprocessed_data: list) -> dict:
    """Extract statistical features from preprocessed data
    
    Dependencies: preprocess_pipeline, calculate_mean, calculate_std_dev, calculate_percentiles
    This is a KEY AGGREGATION NODE that depends on multiple functions
    """
    validated_data = preprocess_pipeline(preprocessed_data)
    
    if not validated_data:
        return {}
    
    features = {
        "mean": calculate_mean(validated_data),
        "std_dev": calculate_std_dev(validated_data),
        "min": min(validated_data),
        "max": max(validated_data),
        "count": len(validated_data),
    }
    
    percentiles = calculate_percentiles(validated_data)
    features.update(percentiles)
    
    return features


# ============================================================================
# Layer 4: Data Transformation (depends on Layer 2 & 3)
# ============================================================================

def apply_normalization(data: list) -> list:
    """Apply min-max normalization
    
    Dependencies: validate_input, normalize_data
    """
    if not validate_input(data):
        raise ValueError("Invalid input for normalization")
    return normalize_data(data)


def apply_scaling(data: list, scale_factor: float = 100) -> list:
    """Scale normalized data by a factor
    
    Dependencies: apply_normalization
    """
    normalized = apply_normalization(data)
    return [x * scale_factor for x in normalized]


def transform_data(raw_data: list, transformation: str = "scale") -> list:
    """Apply transformation pipeline
    
    Dependencies: validate_input, apply_normalization, apply_scaling
    Another KEY AGGREGATION NODE
    """
    if not validate_input(raw_data):
        raise ValueError("Input validation failed in transformation")
    
    if transformation == "normalize":
        return apply_normalization(raw_data)
    elif transformation == "scale":
        return apply_scaling(raw_data)
    else:
        return raw_data


# ============================================================================
# Layer 5: Analysis & Aggregation (depends on Layers 3 & 4)
# ============================================================================

def analyze_distribution(data: list) -> dict:
    """Analyze data distribution
    
    Dependencies: extract_features, transform_data, calculate_mean, calculate_std_dev
    COMPLEX NODE: calls multiple feature extraction functions
    """
    features = extract_features(data)
    transformed = transform_data(data, "normalize")
    
    analysis = {
        "raw_features": features,
        "transformed_count": len(transformed),
        "skewness": "positive" if features.get("mean", 0) > features.get("p50", 0) else "negative",
    }
    
    return analysis


def generate_report(data: list) -> dict:
    """Generate comprehensive analysis report
    
    Dependencies: preprocess_pipeline, extract_features, analyze_distribution, 
                  transform_data, calculate_mean
    MASTER AGGREGATION NODE: depends on MULTIPLE complex nodes
    This creates a rich dependency chain!
    """
    preprocessed = preprocess_pipeline(data)
    features = extract_features(data)
    distribution = analyze_distribution(data)
    transformed = transform_data(data, "scale")
    avg = calculate_mean(data)
    
    report = {
        "input_size": len(data),
        "preprocessed_size": len(preprocessed),
        "features": features,
        "distribution_analysis": distribution,
        "transformed_size": len(transformed),
        "average": avg,
        "status": "success" if preprocessed else "empty_result"
    }
    
    return report


# ============================================================================
# Example Usage demonstrating the dependency chain
# ============================================================================

if __name__ == "__main__":
    sample_data = [10, 15, 12, 18, 20, 100, 16, 14, 19, 17]
    
    print("=" * 70)
    print("Complex Dependency Chain Analysis")
    print("=" * 70)
    
    print("\n[Layer 1] Validation:")
    print(f"  Input valid: {validate_input(sample_data)}")
    print(f"  Within bounds: {check_bounds(sample_data)}")
    
    print("\n[Layer 2] Preprocessing:")
    cleaned = preprocess_pipeline(sample_data)
    print(f"  Original size: {len(sample_data)}, Cleaned size: {len(cleaned)}")
    
    print("\n[Layer 3] Feature Extraction:")
    features = extract_features(sample_data)
    print(f"  Mean: {features.get('mean', 0):.2f}")
    print(f"  Std Dev: {features.get('std_dev', 0):.2f}")
    
    print("\n[Layer 4] Data Transformation:")
    scaled = apply_scaling(sample_data)
    print(f"  Original: {sample_data[:3]}...")
    print(f"  Scaled: {[f'{x:.2f}' for x in scaled[:3]]}...")
    
    print("\n[Layer 5] Full Analysis Report:")
    report = generate_report(sample_data)
    print(f"  Input size: {report['input_size']}")
    print(f"  Status: {report['status']}")
    
    print("\n" + "=" * 70)