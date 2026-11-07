#!/usr/bin/env Rscript
# Pool continuous two-arm outcomes with meta::metacont, overall and by modality.
# Usage: Rscript metacont.R <input_csv> <results_csv> <forest_png> <label> <measure>
# input_csv columns: pmid, modality, n_tx, mean_tx, sd_tx, n_ctrl, mean_ctrl, sd_ctrl

args <- commandArgs(trailingOnly = TRUE)
incsv <- args[1]; outcsv <- args[2]; png_path <- args[3]
label <- if (length(args) >= 4) args[4] else "Continuous outcome"
measure <- if (length(args) >= 5) args[5] else "SMD"

suppressMessages(library(meta))
d <- read.csv(incsv, stringsAsFactors = FALSE)

num <- function(x) {
  if (length(x) == 0 || is.null(x)) return(NA_real_)
  as.numeric(x[1])
}
pct <- function(x) {
  y <- num(x)
  if (!is.na(y) && abs(y) <= 1) y <- y * 100
  y
}
pred <- function(m, name, k) {
  if (k < 3) return(NA_real_)
  num(m[[name]])
}

fit_cont <- function(sub) {
  tryCatch(
    metacont(
      n.e = sub$n_tx, mean.e = sub$mean_tx, sd.e = sub$sd_tx,
      n.c = sub$n_ctrl, mean.c = sub$mean_ctrl, sd.c = sub$sd_ctrl,
      studlab = sub$pmid, sm = measure, method.smd = "Hedges",
      common = FALSE, random = TRUE, method.random.ci = "HK",
      prediction = nrow(sub) >= 3
    ),
    error = function(e) NULL
  )
}

pool <- function(sub, group) {
  if (nrow(sub) < 2) return(NULL)
  m <- fit_cont(sub)
  if (is.null(m)) return(NULL)
  data.frame(
    group = group, k = nrow(sub), measure = measure,
    estimate = round(num(m$TE.random), 4),
    ci_lb = round(num(m$lower.random), 4),
    ci_ub = round(num(m$upper.random), 4),
    Q = round(num(m$Q), 4), I2 = round(pct(m$I2), 1),
    I2_lb = round(pct(m$lower.I2), 1), I2_ub = round(pct(m$upper.I2), 1),
    tau2 = round(num(m$tau2), 4),
    pi_lb = round(pred(m, "lower.predict", nrow(sub)), 4),
    pi_ub = round(pred(m, "upper.predict", nrow(sub)), 4),
    stringsAsFactors = FALSE
  )
}

out <- list()
a <- pool(d, "ALL"); if (!is.null(a)) out[[length(out) + 1]] <- a
for (m in sort(unique(d$modality))) {
  r <- pool(d[d$modality == m, ], m)
  if (!is.null(r)) out[[length(out) + 1]] <- r
}

cols <- c("group", "k", "measure", "estimate", "ci_lb", "ci_ub",
          "Q", "I2", "I2_lb", "I2_ub", "tau2", "pi_lb", "pi_ub")
if (length(out) > 0) {
  write.csv(do.call(rbind, out)[, cols], outcsv, row.names = FALSE, na = "")
} else {
  empty <- as.data.frame(setNames(replicate(length(cols), logical(0), simplify = FALSE), cols))
  write.csv(empty, outcsv, row.names = FALSE)
}

if (nrow(d) >= 2) {
  m <- fit_cont(d)
  if (!is.null(m)) {
    png(png_path, width = 1100, height = max(400, 26 * nrow(d) + 220), res = 130)
    tryCatch(
      forest(m, header = label, xlab = measure,
             studlab = paste0(d$pmid, " (", d$modality, ")")),
      error = function(e) plot.new()
    )
    dev.off()
  }
}
