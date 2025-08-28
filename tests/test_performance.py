"""
Performance tests for OpenSCAD MCP Server.

Tests performance characteristics including:
- Parser performance
- Response size estimation speed
- Large data handling
- Concurrent operations
"""

import pytest
import time
import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import Mock, patch
import asyncio

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from openscad_mcp.server import (
    parse_list_param,
    parse_dict_param,
    parse_image_size_param,
    estimate_response_size,
    manage_response_size,
    compress_base64_image,
    save_image_to_file
)


@pytest.mark.performance
class TestPerformance:
    """Performance tests for critical functions."""
    
    def test_parse_list_param_performance(self):
        """Test parse_list_param completes within acceptable time."""
        test_cases = [
            '["item1", "item2", "item3", "item4", "item5"]',
            "item1,item2,item3,item4,item5",
            ["item1", "item2", "item3", "item4", "item5"],
            "single_item"
        ]
        
        start = time.perf_counter()
        for _ in range(1000):
            for test_case in test_cases:
                parse_list_param(test_case, [])
        elapsed_ms = (time.perf_counter() - start) * 1000
        
        # Should complete 4000 iterations in under 100ms
        assert elapsed_ms < 100, f"Parser took {elapsed_ms}ms, expected < 100ms"
        
        # Calculate operations per second
        ops_per_second = (1000 * len(test_cases)) / (elapsed_ms / 1000)
        print(f"Parse list param: {ops_per_second:.0f} ops/sec")
    
    def test_parse_dict_param_performance(self):
        """Test parse_dict_param completes within acceptable time."""
        test_cases = [
            '{"key1": "value1", "key2": 123, "key3": true}',
            "key1=value1,key2=123,key3=true",
            {"key1": "value1", "key2": 123, "key3": True}
        ]
        
        start = time.perf_counter()
        for _ in range(1000):
            for test_case in test_cases:
                parse_dict_param(test_case, {})
        elapsed_ms = (time.perf_counter() - start) * 1000
        
        # Should complete 3000 iterations in under 150ms
        assert elapsed_ms < 150, f"Parser took {elapsed_ms}ms, expected < 150ms"
        
        ops_per_second = (1000 * len(test_cases)) / (elapsed_ms / 1000)
        print(f"Parse dict param: {ops_per_second:.0f} ops/sec")
    
    def test_parse_image_size_performance(self):
        """Test parse_image_size_param with various formats."""
        test_cases = [
            [800, 600],
            "800x600",
            "800,600",
            (800, 600),
            "[800, 600]"
        ]
        
        start = time.perf_counter()
        for _ in range(1000):
            for test_case in test_cases:
                parse_image_size_param(test_case, [])
        elapsed_ms = (time.perf_counter() - start) * 1000
        
        # Should complete 5000 iterations in under 50ms
        assert elapsed_ms < 50, f"Parser took {elapsed_ms}ms, expected < 50ms"
        
        ops_per_second = (1000 * len(test_cases)) / (elapsed_ms / 1000)
        print(f"Parse image size: {ops_per_second:.0f} ops/sec")
    
    def test_response_size_estimation_performance(self):
        """Test speed of response size estimation."""
        # Small data
        small_data = {"image": "A" * 100}
        
        start = time.perf_counter()
        for _ in range(10000):
            estimate_response_size(small_data)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 50, f"Small estimation took {elapsed_ms}ms"
        
        # Medium data
        medium_data = {
            "images": {"view_" + str(i): "A" * 1000 for i in range(10)}
        }
        
        start = time.perf_counter()
        for _ in range(1000):
            estimate_response_size(medium_data)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 100, f"Medium estimation took {elapsed_ms}ms"
        
        # Large data
        large_data = {
            "images": {"view_" + str(i): "A" * 10000 for i in range(20)},
            "metadata": {"info": "data" * 100}
        }
        
        start = time.perf_counter()
        for _ in range(100):
            estimate_response_size(large_data)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 200, f"Large estimation took {elapsed_ms}ms"
    
    @pytest.mark.slow
    def test_large_batch_parsing(self, performance_test_data):
        """Test parsing performance with large datasets."""
        large_list = performance_test_data["large_list"]
        large_dict = performance_test_data["large_dict"]
        
        # Convert to JSON strings
        large_list_json = json.dumps(large_list)
        large_dict_json = json.dumps(large_dict)
        
        # Test large list parsing
        start = time.perf_counter()
        result = parse_list_param(large_list_json, [])
        list_time = (time.perf_counter() - start) * 1000
        assert len(result) == len(large_list)
        assert list_time < 100, f"Large list parsing took {list_time}ms"
        
        # Test large dict parsing
        start = time.perf_counter()
        result = parse_dict_param(large_dict_json, {})
        dict_time = (time.perf_counter() - start) * 1000
        assert len(result) == len(large_dict)
        assert dict_time < 150, f"Large dict parsing took {dict_time}ms"
    
    @patch('openscad_mcp.server.save_image_to_file')
    def test_manage_response_size_performance(self, mock_save, performance_test_data):
        """Test response management with many images."""
        mock_save.return_value = "/tmp/test.png"
        many_images = performance_test_data["many_images"]
        
        # Auto mode performance
        start = time.perf_counter()
        result = manage_response_size(many_images, output_format="auto", max_size=1000)
        auto_time = (time.perf_counter() - start) * 1000
        assert auto_time < 500, f"Auto mode took {auto_time}ms for {len(many_images)} images"
        
        # File path mode performance
        start = time.perf_counter()
        result = manage_response_size(many_images, output_format="file_path")
        file_time = (time.perf_counter() - start) * 1000
        assert file_time < 200, f"File mode took {file_time}ms"
    
    def test_concurrent_parsing(self):
        """Test thread safety of parsing functions."""
        import concurrent.futures
        import threading
        
        # Test data
        test_lists = [f'["item{i}", "item{i+1}"]' for i in range(100)]
        test_dicts = [f'{{"key{i}": {i}, "key{i+1}": {i+1}}}' for i in range(100)]
        
        results = []
        lock = threading.Lock()
        
        def parse_list_worker(data):
            result = parse_list_param(data, [])
            with lock:
                results.append(("list", result))
        
        def parse_dict_worker(data):
            result = parse_dict_param(data, {})
            with lock:
                results.append(("dict", result))
        
        start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            # Submit all tasks
            list_futures = [
                executor.submit(parse_list_worker, data) 
                for data in test_lists
            ]
            dict_futures = [
                executor.submit(parse_dict_worker, data)
                for data in test_dicts
            ]
            
            # Wait for completion
            concurrent.futures.wait(list_futures + dict_futures)
        
        elapsed = time.perf_counter() - start
        
        assert len(results) == 200
        assert elapsed < 1.0, f"Concurrent parsing took {elapsed}s"
        
        # Verify correctness
        list_results = [r for t, r in results if t == "list"]
        dict_results = [r for t, r in results if t == "dict"]
        assert len(list_results) == 100
        assert len(dict_results) == 100


@pytest.mark.performance
class TestMemoryEfficiency:
    """Test memory efficiency of operations."""
    
    def test_large_image_handling_memory(self, large_base64_image, temp_test_dir):
        """Test memory efficiency when handling large images."""
        import tracemalloc
        
        # Start memory tracking
        tracemalloc.start()
        baseline = tracemalloc.get_traced_memory()[0]
        
        # Process large image
        for _ in range(10):
            save_image_to_file(large_base64_image, f"test_{_}.png", temp_test_dir)
        
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        
        # Memory usage should be reasonable (< 50MB for 10 large images)
        memory_used_mb = (peak - baseline) / 1024 / 1024
        assert memory_used_mb < 50, f"Used {memory_used_mb:.2f}MB, expected < 50MB"
    
    @patch('openscad_mcp.server.Image')
    def test_compression_memory_efficiency(self, mock_image_class):
        """Test memory efficiency of compression."""
        import tracemalloc
        import io
        
        # Mock PIL Image
        mock_image = Mock()
        mock_image_class.open.return_value = mock_image
        
        def save_side_effect(buffer, **kwargs):
            # Simulate saving compressed data
            buffer.write(b"compressed_data" * 100)
        
        mock_image.save.side_effect = save_side_effect
        
        # Large image data
        large_data = "A" * 100000
        test_image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ" + large_data
        
        tracemalloc.start()
        baseline = tracemalloc.get_traced_memory()[0]
        
        # Compress multiple times
        for _ in range(5):
            compress_base64_image(test_image, quality=85)
        
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        
        memory_used_mb = (peak - baseline) / 1024 / 1024
        assert memory_used_mb < 20, f"Compression used {memory_used_mb:.2f}MB"


@pytest.mark.performance
class TestScalability:
    """Test scalability with increasing load."""
    
    def test_parsing_scalability(self):
        """Test how parsing performance scales with input size."""
        sizes = [10, 50, 100, 500, 1000]
        times = []
        
        for size in sizes:
            # Generate test data
            test_list = list(range(size))
            test_json = json.dumps(test_list)
            
            # Measure parsing time
            start = time.perf_counter()
            for _ in range(100):
                parse_list_param(test_json, [])
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            
            print(f"Size {size}: {elapsed:.4f}s")
        
        # Check that time increases sub-linearly (good scalability)
        # Time should not increase more than O(n log n)
        for i in range(1, len(sizes)):
            size_ratio = sizes[i] / sizes[i-1]
            time_ratio = times[i] / times[i-1]
            # Allow for some variance but ensure sub-quadratic growth
            assert time_ratio < size_ratio * 2, \
                f"Poor scalability: {size_ratio}x size -> {time_ratio}x time"
    
    def test_response_size_estimation_scalability(self):
        """Test estimation performance with increasing data size."""
        sizes = [100, 1000, 10000, 50000]
        times = []
        
        for size in sizes:
            data = {"data": "A" * size}
            
            start = time.perf_counter()
            for _ in range(1000):
                estimate_response_size(data)
            elapsed = time.perf_counter() - start
            times.append(elapsed)
            
            print(f"Data size {size}: {elapsed:.4f}s for 1000 estimations")
        
        # Linear scalability is acceptable for estimation
        for i in range(1, len(sizes)):
            size_ratio = sizes[i] / sizes[i-1]
            time_ratio = times[i] / times[i-1]
            assert time_ratio < size_ratio * 1.5, \
                f"Poor scalability at size {sizes[i]}"


@pytest.mark.performance
@pytest.mark.asyncio
class TestAsyncPerformance:
    """Test performance of async operations."""
    
    async def test_concurrent_renders(self):
        """Test concurrent render operations."""
        from openscad_mcp.server import render_single
        
        with patch('openscad_mcp.server.render_scad_to_png') as mock_render:
            mock_render.return_value = "base64imagedata"
            
            # Create multiple render tasks
            tasks = []
            for i in range(10):
                task = render_single(
                    scad_content=f"cube({i+10});",
                    camera_position=[i*10, i*10, i*10]
                )
                tasks.append(task)
            
            # Run concurrently
            start = time.perf_counter()
            results = await asyncio.gather(*tasks)
            elapsed = time.perf_counter() - start
            
            assert len(results) == 10
            assert all(r["success"] for r in results)
            assert elapsed < 1.0, f"Concurrent renders took {elapsed}s"
    
    async def test_render_queue_performance(self):
        """Test performance with queued renders."""
        from openscad_mcp.server import render_single
        
        with patch('openscad_mcp.server.render_scad_to_png') as mock_render:
            # Simulate varying render times
            mock_render.side_effect = lambda *args: "base64data"
            
            tasks = []
            views = ["front", "top", "isometric", "left", "right"]
            for i, view in enumerate(views):
                task = render_single(
                    scad_content=f"sphere({i*5});",
                    view=view
                )
                tasks.append(task)
            
            start = time.perf_counter()
            results = await asyncio.gather(*tasks)
            elapsed = time.perf_counter() - start
            
            assert len(results) == 5
            # Should handle 5 single renders efficiently
            assert elapsed < 2.0, f"Queue processing took {elapsed}s"


def benchmark_all_parsers():
    """Benchmark all parser functions."""
    results = {}
    iterations = 10000
    
    # Benchmark parse_list_param
    test_list = '["item1", "item2", "item3"]'
    start = time.perf_counter()
    for _ in range(iterations):
        parse_list_param(test_list, [])
    results["parse_list_param"] = (time.perf_counter() - start) * 1000
    
    # Benchmark parse_dict_param
    test_dict = '{"key1": "value1", "key2": 123}'
    start = time.perf_counter()
    for _ in range(iterations):
        parse_dict_param(test_dict, {})
    results["parse_dict_param"] = (time.perf_counter() - start) * 1000
    
    # Benchmark parse_image_size_param
    test_size = "800x600"
    start = time.perf_counter()
    for _ in range(iterations):
        parse_image_size_param(test_size, [])
    results["parse_image_size_param"] = (time.perf_counter() - start) * 1000
    
    # Print results
    print("\n=== Parser Benchmarks ===")
    print(f"Iterations: {iterations}")
    for func, time_ms in results.items():
        ops_per_sec = iterations / (time_ms / 1000)
        print(f"{func}: {time_ms:.2f}ms total, {ops_per_sec:.0f} ops/sec")
    
    return results


if __name__ == "__main__":
    # Run benchmarks when executed directly
    benchmark_all_parsers()