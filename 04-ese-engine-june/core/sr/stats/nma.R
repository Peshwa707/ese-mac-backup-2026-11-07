#!/usr/bin/env Rscript
# Bayesian random-effects network meta-analysis via gemtc (contrast / data.re).
# Usage: Rscript nma.R <edges_csv> <league_csv> <network_png>
# edges_csv columns: study, treatment, diff, std.err  (reference arm has diff=NA)

args <- commandArgs(trailingOnly = TRUE)
incsv <- args[1]; outcsv <- args[2]; png_path <- args[3]

suppressMessages(library(gemtc))
d <- read.csv(incsv, stringsAsFactors = FALSE)
d$diff <- suppressWarnings(as.numeric(d$diff))
d$std.err <- suppressWarnings(as.numeric(d$std.err))

network <- mtc.network(data.re = d)
tryCatch({
  png(png_path, width = 720, height = 600, res = 120)
  plot(network)
  dev.off()
}, error = function(e) NULL)

model <- mtc.model(network, type = "consistency", linearModel = "random", n.chain = 3)
res <- mtc.run(model, n.adapt = 2000, n.iter = 10000, thin = 1)

trts <- as.character(network$treatments$id)
rows <- list()
for (i in trts) for (j in trts) if (i != j) {
  re <- tryCatch(summary(relative.effect(res, t1 = i, t2 = j))$summaries$quantiles,
                 error = function(e) NULL)
  if (is.null(re)) next
  q <- re[1, ]
  rows[[length(rows) + 1]] <- data.frame(
    comparison = paste(j, "vs", i),
    estimate = round(q["50%"], 3), ci_lb = round(q["2.5%"], 3), ci_ub = round(q["97.5%"], 3),
    stringsAsFactors = FALSE)
}

if (length(rows) > 0) {
  write.csv(do.call(rbind, rows), outcsv, row.names = FALSE)
} else {
  write.csv(data.frame(comparison = character(), estimate = numeric(),
                       ci_lb = numeric(), ci_ub = numeric()), outcsv, row.names = FALSE)
}
