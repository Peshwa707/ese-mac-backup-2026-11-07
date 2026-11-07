#!/usr/bin/env Rscript
# Pool 2x2 binary outcomes with meta::metabin, overall and by modality.
# Usage: Rscript metabin.R <input_csv> <results_csv> <forest_png> <label> <measure> <method>
# input_csv columns: pmid, modality, events_tx, n_tx, events_ctrl, n_ctrl

args <- commandArgs(trailingOnly = TRUE)
incsv <- args[1]; outcsv <- args[2]; png_path <- args[3]
label <- if (length(args) >= 4) args[4] else "Binary outcome"
measure <- if (length(args) >= 5) args[5] else "RR"
method <- if (length(args) >= 6) args[6] else "MH"

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
bt <- function(x, sm) {
  y <- num(x)
  if (!is.na(y) && sm %in% c("RR", "OR")) y <- exp(y)
  y
}
pred <- function(m, name, sm, k) {
  if (k < 3) return(NA_real_)
  bt(m[[name]], sm)
}
nn <- function(rd, rd_lb, rd_ub) {
  if (is.na(rd) || is.na(rd_lb) || is.na(rd_ub) || rd_lb <= 0 && rd_ub >= 0 || rd == 0) {
    return(NA_real_)
  }
  abs(1 / rd)
}

fit_bin <- function(sub, sm) {
  tryCatch(
    metabin(
      event.e = sub$events_tx, n.e = sub$n_tx,
      event.c = sub$events_ctrl, n.c = sub$n_ctrl,
      studlab = sub$pmid, sm = sm, method = method,
      common = FALSE, random = TRUE, method.random.ci = "HK",
      prediction = nrow(sub) >= 3
    ),
    error = function(e) NULL
  )
}

pool <- function(sub, group) {
  if (nrow(sub) < 2) return(NULL)
  m <- fit_bin(sub, measure)
  rd <- fit_bin(sub, "RD")
  if (is.null(m) || is.null(rd)) return(NULL)
  rd_est <- num(rd$TE.random)
  rd_lb <- num(rd$lower.random)
  rd_ub <- num(rd$upper.random)
  data.frame(
    group = group, k = nrow(sub), measure = measure,
    estimate = round(bt(m$TE.random, measure), 4),
    ci_lb = round(bt(m$lower.random, measure), 4),
    ci_ub = round(bt(m$upper.random, measure), 4),
    rd = round(rd_est, 4), rd_lb = round(rd_lb, 4), rd_ub = round(rd_ub, 4),
    nnt = round(nn(rd_est, rd_lb, rd_ub), 1),
    Q = round(num(m$Q), 4), I2 = round(pct(m$I2), 1),
    I2_lb = round(pct(m$lower.I2), 1), I2_ub = round(pct(m$upper.I2), 1),
    tau2 = round(num(m$tau2), 4),
    pi_lb = round(pred(m, "lower.predict", measure, nrow(sub)), 4),
    pi_ub = round(pred(m, "upper.predict", measure, nrow(sub)), 4),
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
          "rd", "rd_lb", "rd_ub", "nnt", "Q", "I2", "I2_lb", "I2_ub",
          "tau2", "pi_lb", "pi_ub")
if (length(out) > 0) {
  write.csv(do.call(rbind, out)[, cols], outcsv, row.names = FALSE, na = "")
} else {
  empty <- as.data.frame(setNames(replicate(length(cols), logical(0), simplify = FALSE), cols))
  write.csv(empty, outcsv, row.names = FALSE)
}

if (nrow(d) >= 2) {
  m <- fit_bin(d, measure)
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
