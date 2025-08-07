import json
import hashlib
from datetime import datetime, timedelta
import os
from typing import Optional, Any

# Simple file-based cache (consider using Redis for production)
CACHE_DIR = "cache"
CACHE_DURATION_HOURS = 24  # Cache results for 24 hours

def ensure_cache_dir():
    """Ensure cache directory exists"""
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)

def get_cache_key(*args, **kwargs) -> str:
    """Generate a cache key from arguments"""
    key_data = f"{args}{kwargs}"
    return hashlib.md5(key_data.encode()).hexdigest()

def get_cached_result(*args, **kwargs) -> Optional[Any]:
    """Get cached result if it exists and is not expired"""
    ensure_cache_dir()
    
    cache_key = get_cache_key(*args, **kwargs)
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.json")
    
    if not os.path.exists(cache_file):
        return None
    
    try:
        with open(cache_file, 'r') as f:
            cached_data = json.load(f)
        
        # Check if cache has expired
        cached_time = datetime.fromisoformat(cached_data["timestamp"])
        if datetime.now() - cached_time > timedelta(hours=CACHE_DURATION_HOURS):
            # Cache expired, remove file
            os.remove(cache_file)
            return None
        
        return cached_data["result"]
        
    except (json.JSONDecodeError, KeyError, ValueError):
        # Invalid cache file, remove it
        if os.path.exists(cache_file):
            os.remove(cache_file)
        return None

def store_result(cache_key_args, result: Any):
    """Store result in cache"""
    ensure_cache_dir()
    
    if isinstance(cache_key_args, str):
        cache_key = hashlib.md5(cache_key_args.encode()).hexdigest()
    else:
        cache_key = get_cache_key(cache_key_args)
    
    cache_file = os.path.join(CACHE_DIR, f"{cache_key}.json")
    
    cache_data = {
        "timestamp": datetime.now().isoformat(),
        "result": result
    }
    
    try:
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f, indent=2, default=str)
    except Exception as e:
        print(f"Error storing cache: {e}")

def clear_cache():
    """Clear all cached results"""
    if os.path.exists(CACHE_DIR):
        for filename in os.listdir(CACHE_DIR):
            if filename.endswith('.json'):
                os.remove(os.path.join(CACHE_DIR, filename))

def get_cache_stats():
    """Get cache statistics"""
    if not os.path.exists(CACHE_DIR):
        return {"cached_items": 0, "cache_size_mb": 0}
    
    files = [f for f in os.listdir(CACHE_DIR) if f.endswith('.json')]
    total_size = sum(os.path.getsize(os.path.join(CACHE_DIR, f)) for f in files)
    
    return {
        "cached_items": len(files),
        "cache_size_mb": round(total_size / (1024 * 1024), 2)
    }
