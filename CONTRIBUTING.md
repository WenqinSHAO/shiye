# Contributing to Shiye

Thank you for considering contributing to Shiye! This document provides guidelines and instructions for contributing to the project.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Testing](#testing)
- [Code Style](#code-style)
- [Submitting Changes](#submitting-changes)
- [Areas for Contribution](#areas-for-contribution)

## Code of Conduct

- Be respectful and constructive in discussions
- Welcome newcomers and help them get started
- Focus on what is best for the project and community
- Show empathy towards other community members

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/your-username/shiye.git
   cd shiye
   ```
3. **Set up the upstream remote**:
   ```bash
   git remote add upstream https://github.com/WenqinSHAO/shiye.git
   ```

## Development Setup

### Prerequisites

- Python 3.10 or higher
- pip (Python package manager)
- Git

### Installing Dependencies

```bash
# Create a virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install development dependencies
pip install pytest black ruff mypy
```

### Environment Configuration

Create a `.env` file or set environment variables:

```bash
# Required for LLM functionality
export DS_API_KEY="your-deepseek-api-key"

# Optional configuration
export SHIYE_DATA_DIR="~/.shiye"
export SHIYE_EMBED_MODEL="sentence-transformers/all-MiniLM-L6-v2"
```

### Running the Application

```bash
# Start the web server
python main.py

# Or with auto-reload for development
uvicorn web:app --reload --port 8000
```

Visit `http://localhost:8000` to verify the setup.

## Making Changes

### Branch Naming

Use descriptive branch names:
- `feature/add-pdf-support` - New features
- `fix/storage-race-condition` - Bug fixes
- `docs/update-readme` - Documentation updates
- `refactor/simplify-orchestrator` - Code refactoring

### Commit Messages

Follow these guidelines:
- Use present tense ("Add feature" not "Added feature")
- Use imperative mood ("Move cursor to..." not "Moves cursor to...")
- First line should be concise (50 chars or less)
- Add detailed description if needed, separated by a blank line

Example:
```
Add PDF ingestion support

- Implement PDF text extraction using PyPDF2
- Add tests for PDF parsing
- Update documentation with PDF usage examples
```

## Testing

### Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_storage.py -v

# Run with coverage
python -m pytest tests/ --cov=. --cov-report=html
```

### Writing Tests

- Place tests in the `tests/` directory
- Name test files as `test_<module>.py`
- Use descriptive test function names: `test_fetch_url_extracts_title()`
- Include both positive and negative test cases
- Use fixtures for common setup

Example:
```python
def test_extract_urls_finds_multiple_urls():
    text = "Check https://example.com and https://test.org"
    urls = extract_urls(text)
    assert len(urls) == 2
    assert "https://example.com" in urls
    assert "https://test.org" in urls
```

## Code Style

### Python Style Guide

- Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/) style guide
- Use 4 spaces for indentation (no tabs)
- Maximum line length: 120 characters
- Use type hints where appropriate

### Formatting Tools

```bash
# Format code with Black
black *.py

# Check code style with Ruff
ruff check .

# Type checking with mypy
mypy *.py
```

### Docstrings

Use Google-style docstrings:

```python
def fetch_url_content(url: str) -> Tuple[str, str, str]:
    """Fetch and extract content from a URL.
    
    Args:
        url: The URL to fetch content from.
        
    Returns:
        A tuple of (title, content, extraction_method).
        
    Raises:
        requests.RequestException: If the URL cannot be fetched.
    """
    # Implementation here
```

## Submitting Changes

### Pull Request Process

1. **Update your fork** with the latest changes:
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Create a pull request**:
   - Provide a clear title and description
   - Reference any related issues
   - Include screenshots for UI changes
   - List breaking changes if any

3. **PR Description Template**:
   ```markdown
   ## Description
   Brief description of the changes
   
   ## Type of Change
   - [ ] Bug fix
   - [ ] New feature
   - [ ] Documentation update
   - [ ] Refactoring
   
   ## Testing
   Description of tests added/modified
   
   ## Checklist
   - [ ] Tests pass locally
   - [ ] Code follows style guidelines
   - [ ] Documentation updated
   - [ ] No breaking changes (or documented)
   ```

4. **Review Process**:
   - Address reviewer feedback promptly
   - Make requested changes in new commits
   - Keep the PR focused and reasonably sized

## Areas for Contribution

We welcome contributions in these areas:

### High Priority

- **UI/UX Improvements**: Better error handling, loading states, notifications
- **Search Functionality**: Expose semantic search in the UI
- **Note Features**: Autosave, tagging, export capabilities
- **Test Coverage**: Add tests for untested modules

### Medium Priority

- **Additional Ingest Formats**: PDF, EPUB, email support
- **LLM Provider Support**: OpenAI, Anthropic, local models
- **Performance**: Optimize retrieval, caching strategies
- **Documentation**: Code examples, tutorials, API docs

### Long Term

- **Tool Execution**: Safe code execution sandbox
- **Multi-device Sync**: Encrypted synchronization
- **Timeline View**: Visual timeline interface
- **Proactive Features**: Reminders and suggestions

## Questions?

- Open an issue for bugs or feature requests
- Start a discussion for questions or ideas
- Check existing issues and PRs to avoid duplicates

## License

By contributing, you agree that your contributions will be licensed under the same license as the project.

Thank you for contributing to Shiye! 🎉
