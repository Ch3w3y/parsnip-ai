# ── Stage 1: R base with pre-compiled packages ────────────────────────────────
# rocker/tidyverse:4.4.2 — last stable tag (Ubuntu 22.04 jammy)
FROM rocker/tidyverse:4.4.2 AS r-base

# rocker/tidyverse already includes:
#   tidyverse, data.table, arrow, fst, DBI, RPostgres, RSQLite, dbplyr,
#   rmarkdown, devtools, BiocManager, duckdb, dtplyr, vroom, gert
#   + system libs: libpq-dev, libcurl4-openssl-dev, libxml2-dev, libcairo2-dev, etc.

# Install additional system dependencies not in rocker/tidyverse
# Python 3.11 is available in Ubuntu 22.04 repos — no PPA needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgsl-dev libgmp-dev libfftw3-dev \
    libhdf5-dev libnetcdf-dev libudunits2-dev \
    libgeos-dev libproj-dev libgdal-dev \
    libxt-dev libx11-dev \
    libglpk-dev \
    git wget curl \
    python3 python3-venv python3-dev python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Install additional R packages NOT included in rocker/tidyverse
# Grouped by domain for better layer caching
RUN install2.r --error --skipinstalled -n -1 \
    tidymodels parsnip recipes rsample yardstick \
    glmnet randomForest xgboost lightgbm ranger \
    e1071 kernlab nnet lme4 nlme \
    survival survminer cmprsk \
    MASS car emmeans multcomp pwr \
    ggrepel patchwork cowplot scales ggthemes \
    ggridges ggforce plotly highcharter echarts4r \
    leaflet tmap ggpubr rstatix \
    effectsize performance parameters see \
    broom modelr janitor skimr \
    odbc htmltools flexdashboard shiny \
    pheatmap corrplot FactoMineR factoextra \
    ape phangorn phytools \
    seqinr adegenet vegan picante \
    betapart indicspecies labdsv \
    tm quanteda text2vec tidytext \
    udpipe tokenizers topicmodels stm \
    quanteda.textmodels quanteda.textplots \
    forecast tsibble fable prophet \
    tseries zoo xts spatstat spdep \
    gstat igraph tidygraph ggraph \
    network statnet sna \
    jsonlite xml2 rvest httr2 curl \
    readr readxl writexl \
    testthat microbenchmark profvis bench \
    renv packrat roxygen2 usethis \
    && rm -rf /tmp/downloaded_packages

# Install Bioconductor packages (separate step for clarity)
RUN R -e "BiocManager::install(c(\
    'DESeq2','edgeR','limma','clusterProfiler','pathview',\
    'Biostrings','GenomicRanges','IRanges','SummarizedExperiment',\
    'SingleCellExperiment','Seurat','scran','scater',\
    'slingshot','tradeSeq','GEOquery','TCGAbiolinks',\
    'ggtree','treeio'\
    ), ask=FALSE, update=FALSE, Ncpus=4)"

# ── Stage 2: Python environment ───────────────────────────────────────────────
FROM r-base AS analysis

WORKDIR /app

# Use system Python (3.12 on Ubuntu 24.04 Noble)
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/output

# Initialize git repo for version control of analysis scripts
RUN git init && \
    git config user.email "agent@pi-agent.local" && \
    git config user.name "Research Agent"

EXPOSE 8095

CMD ["python", "server.py"]
