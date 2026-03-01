#!/bin/bash
# File: /Users/mac/Local Sites/chatbotdemo/app/public/wp-content/plugins/custom-openai-chatbot/process_my_policy.sh

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Your Conda path
CONDA_PATH="/opt/anaconda3"
PYTHON_PATH="$CONDA_PATH/envs/rag_env/bin/python"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}🚀 Knowledge Base Manager${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${BLUE}Python: ${NC}$PYTHON_PATH"
echo -e "${BLUE}Plugin: ${NC}$SCRIPT_DIR"
echo ""

# Check if Python exists
if [ ! -f "$PYTHON_PATH" ]; then
    echo -e "${RED}❌ Python not found at $PYTHON_PATH${NC}"
    exit 1
fi

# Check Ollama
echo -e "${BLUE}Checking Ollama...${NC}"
OLLAMA_CHECK=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:11434/api/tags 2>/dev/null)
if [ "$OLLAMA_CHECK" = "200" ]; then
    echo -e "${GREEN}✅ Ollama is running${NC}"
else
    echo -e "${RED}❌ Ollama is not running${NC}"
    echo -e "${YELLOW}⚠️  Run: ollama serve${NC}"
fi
echo ""

# Main menu
while true; do
    echo ""
    echo "Select operation:"
    echo "  1) Process a PDF file"
    echo "  2) Add a website"
    echo "  3) List all sources"
    echo "  4) Remove a source"
    echo "  5) Test a query"
    echo "  6) Retrain all sources"
    echo "  7) Check system status"
    echo "  8) Exit"
    read -p "Enter choice (1-8): " choice
    
    case $choice in
        1)
            echo ""
            echo "PDF files in Downloads folder:"
            ls -la ~/Downloads/*.pdf 2>/dev/null | head -10
            echo ""
            read -p "Enter path to PDF: " pdf_path
            if [ -f "$pdf_path" ]; then
                echo -e "\n${GREEN}🔧 Processing PDF...${NC}\n"
                cd "$SCRIPT_DIR" && $PYTHON_PATH rag_processor/rag_processor.py --pdf "$pdf_path"
            else
                echo -e "${RED}❌ File not found${NC}"
            fi
            ;;
        2)
            read -p "Enter website URL: " website_url
            read -p "Max pages to crawl (default 20): " max_pages
            max_pages=${max_pages:-20}
            echo -e "\n${GREEN}🌐 Crawling website...${NC}\n"
            cd "$SCRIPT_DIR" && $PYTHON_PATH rag_processor/rag_processor.py --website "$website_url" --max-pages $max_pages
            ;;
        3)
            echo -e "\n${GREEN}📚 Listing sources...${NC}\n"
            cd "$SCRIPT_DIR" && $PYTHON_PATH rag_processor/rag_processor.py --list
            ;;
        4)
            read -p "Enter source name or ID to remove: " source_name
            echo -e "\n${GREEN}🗑️ Removing source...${NC}\n"
            cd "$SCRIPT_DIR" && $PYTHON_PATH rag_processor/rag_processor.py --remove "$source_name"
            ;;
        5)
            read -p "Enter your question: " question
            echo -e "\n${GREEN}🔍 Searching...${NC}\n"
            cd "$SCRIPT_DIR" && $PYTHON_PATH rag_processor/rag_processor.py --query "$question"
            ;;
        6)
            echo -e "\n${GREEN}🔄 Retraining all sources...${NC}\n"
            # Clear database
            rm -f "$SCRIPT_DIR/rag_processor/vector_db/chroma.sqlite3"
            rm -f "$SCRIPT_DIR/rag_processor/vector_db/sources.json"
            
            # Reprocess all PDFs
            for pdf in "$SCRIPT_DIR/documents/"*.pdf; do
                if [ -f "$pdf" ]; then
                    echo -e "\n${GREEN}📄 Reprocessing: $(basename "$pdf")${NC}"
                    cd "$SCRIPT_DIR" && $PYTHON_PATH rag_processor/rag_processor.py --pdf "$pdf"
                fi
            done
            
            # Reprocess all websites
            for website_file in "$SCRIPT_DIR/websites/"*.json; do
                if [ -f "$website_file" ]; then
                    url=$(cat "$website_file" | grep -o '"source_url":"[^"]*"' | cut -d'"' -f4)
                    if [ ! -z "$url" ]; then
                        echo -e "\n${GREEN}🌐 Reprocessing: $url${NC}"
                        cd "$SCRIPT_DIR" && $PYTHON_PATH rag_processor/rag_processor.py --website "$url" --max-pages 5
                    fi
                fi
            done
            echo -e "\n${GREEN}✅ Retraining complete!${NC}"
            ;;
        7)
            echo -e "\n${GREEN}🔍 System Status${NC}\n"
            cd "$SCRIPT_DIR" && $PYTHON_PATH rag_processor/rag_processor.py --check
            ;;
        8)
            echo -e "\n${GREEN}Goodbye!${NC}"
            exit 0
            ;;
        *)
            echo -e "${RED}Invalid choice${NC}"
            ;;
    esac
done