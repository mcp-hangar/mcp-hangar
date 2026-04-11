"""Tests for provider repository."""

import threading
import time
from unittest.mock import Mock

import pytest

from mcp_hangar.domain import InMemoryProviderRepository, IProviderRepository
from mcp_hangar.domain.model import Provider


@pytest.fixture
def repository():
    """Create a fresh repository for each test."""
    return InMemoryProviderRepository()


@pytest.fixture
def mock_provider():
    """Create a mock provider."""
    mock = Mock()
    mock.provider_id = "test-provider"
    return mock


@pytest.fixture
def mock_provider_2():
    """Create a second mock provider."""
    mock = Mock()
    mock.provider_id = "test-provider-2"
    return mock


# Basic Operations Tests


def test_repository_implements_interface(repository):
    """Test that repository implements IProviderRepository."""
    assert isinstance(repository, IProviderRepository)


def test_add_provider(repository, mock_provider):
    """Test adding a provider to the repository."""
    repository.add("test-provider", mock_provider)

    assert repository.exists("test-provider")
    assert repository.count() == 1
    assert repository.get("test-provider") == mock_provider


def test_add_provider_empty_id_raises_error(repository, mock_provider):
    """Test that adding provider with empty ID raises ValueError."""
    with pytest.raises(ValueError, match="cannot be empty"):
        repository.add("", mock_provider)


def test_add_provider_overwrites_existing(repository, mock_provider, mock_provider_2):
    """Test that adding provider with existing ID overwrites it."""
    repository.add("same-id", mock_provider)
    repository.add("same-id", mock_provider_2)

    assert repository.count() == 1
    assert repository.get("same-id") == mock_provider_2


def test_get_nonexistent_provider_returns_none(repository):
    """Test that getting non-existent provider returns None."""
    assert repository.get("nonexistent") is None


def test_exists_empty_repository(repository):
    """Test exists returns False for empty repository."""
    assert not repository.exists("any-provider")


def test_exists_after_add(repository, mock_provider):
    """Test exists returns True after adding provider."""
    repository.add("test-provider", mock_provider)
    assert repository.exists("test-provider")


def test_exists_after_remove(repository, mock_provider):
    """Test exists returns False after removing provider."""
    repository.add("test-provider", mock_provider)
    repository.remove("test-provider")
    assert not repository.exists("test-provider")


# Remove Operations Tests


def test_remove_existing_provider(repository, mock_provider):
    """Test removing an existing provider."""
    repository.add("test-provider", mock_provider)

    result = repository.remove("test-provider")

    assert result is True
    assert not repository.exists("test-provider")
    assert repository.count() == 0


def test_remove_nonexistent_provider(repository):
    """Test removing non-existent provider returns False."""
    result = repository.remove("nonexistent")
    assert result is False


# Collection Operations Tests


def test_get_all_empty_repository(repository):
    """Test get_all returns empty dict for empty repository."""
    all_providers = repository.get_all()
    assert all_providers == {}


def test_get_all_with_providers(repository, mock_provider, mock_provider_2):
    """Test get_all returns all providers."""
    repository.add("provider-1", mock_provider)
    repository.add("provider-2", mock_provider_2)

    all_providers = repository.get_all()

    assert len(all_providers) == 2
    assert all_providers["provider-1"] == mock_provider
    assert all_providers["provider-2"] == mock_provider_2


def test_get_all_returns_copy(repository, mock_provider):
    """Test that get_all returns a copy, not the internal dict."""
    repository.add("provider-1", mock_provider)

    all_providers = repository.get_all()
    all_providers["provider-2"] = Mock()  # Modify the returned dict

    # Original repository should be unchanged
    assert repository.count() == 1
    assert not repository.exists("provider-2")


def test_get_all_ids_empty_repository(repository):
    """Test get_all_ids returns empty list for empty repository."""
    ids = repository.get_all_ids()
    assert ids == []


def test_get_all_ids_with_providers(repository, mock_provider, mock_provider_2):
    """Test get_all_ids returns all provider IDs."""
    repository.add("provider-1", mock_provider)
    repository.add("provider-2", mock_provider_2)

    ids = repository.get_all_ids()

    assert len(ids) == 2
    assert "provider-1" in ids
    assert "provider-2" in ids


def test_count_empty_repository(repository):
    """Test count returns 0 for empty repository."""
    assert repository.count() == 0


def test_count_with_providers(repository, mock_provider, mock_provider_2):
    """Test count returns correct number of providers."""
    repository.add("provider-1", mock_provider)
    assert repository.count() == 1

    repository.add("provider-2", mock_provider_2)
    assert repository.count() == 2

    repository.remove("provider-1")
    assert repository.count() == 1


def test_clear_empty_repository(repository):
    """Test clear on empty repository doesn't error."""
    repository.clear()
    assert repository.count() == 0


def test_clear_with_providers(repository, mock_provider, mock_provider_2):
    """Test clear removes all providers."""
    repository.add("provider-1", mock_provider)
    repository.add("provider-2", mock_provider_2)

    repository.clear()

    assert repository.count() == 0
    assert not repository.exists("provider-1")
    assert not repository.exists("provider-2")


# Magic Methods Tests


def test_contains_operator(repository, mock_provider):
    """Test 'in' operator works with repository."""
    repository.add("test-provider", mock_provider)

    assert "test-provider" in repository
    assert "nonexistent" not in repository


def test_len_function(repository, mock_provider, mock_provider_2):
    """Test len() function works with repository."""
    assert len(repository) == 0

    repository.add("provider-1", mock_provider)
    assert len(repository) == 1

    repository.add("provider-2", mock_provider_2)
    assert len(repository) == 2


def test_repr(repository, mock_provider):
    """Test string representation."""
    assert "InMemoryProviderRepository" in repr(repository)
    assert "providers=0" in repr(repository)

    repository.add("provider-1", mock_provider)
    assert "providers=1" in repr(repository)


# Thread Safety Tests


def test_concurrent_adds(repository):
    """Test that concurrent adds are thread-safe."""
    num_threads = 10
    providers_per_thread = 100

    def add_providers(thread_id):
        for i in range(providers_per_thread):
            provider_id = f"provider-{thread_id}-{i}"
            mock = Mock()
            repository.add(provider_id, mock)

    threads = []
    for i in range(num_threads):
        thread = threading.Thread(target=add_providers, args=(i,))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    expected_count = num_threads * providers_per_thread
    assert repository.count() == expected_count


def test_concurrent_reads_and_writes(repository, mock_provider):
    """Test that concurrent reads and writes are thread-safe."""
    repository.add("initial-provider", mock_provider)

    results = []
    errors = []

    def reader():
        for _ in range(100):
            try:
                all_providers = repository.get_all()
                results.append(len(all_providers))
            except Exception as e:
                errors.append(e)

    def writer(thread_id):
        for i in range(50):
            try:
                # Use unique IDs per thread to avoid overwrites
                repository.add(f"provider-{thread_id}-{i}", Mock())
            except Exception as e:
                errors.append(e)

    reader_threads = [threading.Thread(target=reader) for _ in range(5)]
    writer_threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]

    all_threads = reader_threads + writer_threads
    for thread in all_threads:
        thread.start()
    for thread in all_threads:
        thread.join()

    # Should be no errors
    assert len(errors) == 0

    # All reads should have succeeded
    assert len(results) == 500  # 5 threads * 100 reads

    # Final count should be correct (initial + 5 writers * 50 each)
    assert repository.count() == 1 + (5 * 50)


def test_concurrent_remove(repository):
    """Test that concurrent removes are thread-safe."""
    # Add 100 providers
    for i in range(100):
        repository.add(f"provider-{i}", Mock())

    removed_count = [0]
    lock = threading.Lock()

    def remover(start, end):
        local_removed = 0
        for i in range(start, end):
            if repository.remove(f"provider-{i}"):
                local_removed += 1
        with lock:
            removed_count[0] += local_removed

    threads = [
        threading.Thread(target=remover, args=(0, 50)),
        threading.Thread(target=remover, args=(50, 100)),
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert removed_count[0] == 100
    assert repository.count() == 0


def test_clear_during_concurrent_access(repository):
    """Test that clear is safe during concurrent access."""
    # Add some initial providers
    for i in range(50):
        repository.add(f"provider-{i}", Mock())

    errors = []

    def continuous_reader():
        for _ in range(100):
            try:
                repository.get_all()
                time.sleep(0.001)
            except Exception as e:
                errors.append(e)

    def clear_repository():
        time.sleep(0.01)  # Let readers start
        repository.clear()

    reader_threads = [threading.Thread(target=continuous_reader) for _ in range(3)]
    clear_thread = threading.Thread(target=clear_repository)

    all_threads = reader_threads + [clear_thread]
    for thread in all_threads:
        thread.start()
    for thread in all_threads:
        thread.join()

    # Should be no errors
    assert len(errors) == 0

    # Repository should be empty after clear
    assert repository.count() == 0


# Integration with Real Provider


def test_with_real_provider():
    """Test repository with real Provider instances."""
    repository = InMemoryProviderRepository()

    provider1 = Provider(
        provider_id="math-provider",
        mode="subprocess",
        command=["python", "-m", "math_provider"],
    )

    provider2 = Provider(
        provider_id="weather-provider",
        mode="subprocess",
        command=["python", "-m", "weather_provider"],
    )

    # Add providers
    repository.add("math-provider", provider1)
    repository.add("weather-provider", provider2)

    # Verify storage
    assert repository.count() == 2
    assert repository.get("math-provider") == provider1
    assert repository.get("weather-provider") == provider2

    # Verify retrieval
    all_providers = repository.get_all()
    assert len(all_providers) == 2
    assert all_providers["math-provider"].provider_id == "math-provider"
    assert all_providers["weather-provider"].provider_id == "weather-provider"
