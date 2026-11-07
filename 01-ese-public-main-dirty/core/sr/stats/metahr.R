#!/usr/bin/env Rscript
# Pool hazard ratios by inverse-variance random-effects (REML) on log-HR, overall
# and by modality. Writes a results CSV (back-transformed HR + CI) + forest PNG.
# Usage: Rscript metahr.R <input_csv> <results_csv> <forest_png> <label>
# input_csv columns: pmid, modality, yi (logHR), vi (variance)

args <- commandArgs(trailingOnly = TRUE)
incsv <- args[1]; outcsv <- args[2]; png_path <- args[3]
label <- if (length(args) >= 4) args[4] else "Pooled hazard ratio"

suppressMessages(library(metafor))
d <- read.csv(incsv, stringsAsFactors = FALSE)

pool <- function(sub, group) {
  if (nrow(sub) < 2) return(NULL)
  res <- tryCatch(rma(yi = sub$yi, vi = sub$vi, method = "REML"), error = function(e) NULL)
  if (is.null(res)) return(NULL)
  pred <- tryCatch(predict(res, transf = exp), error = function(e) NULL)
  pi_lb <- if (nrow(sub) >= 3 && !is.null(pred) && !is.null(pred$pi.lb)) pred$pi.lb else NA_real_
  pi_ub <- if (nrow(sub) >= 3 && !is.null(pred) && !is.null(pred$pi.ub)) pred$pi.ub else NA_real_
  data.frame(group = group, k = nrow(sub),
             hr = round(exp(res$b[1]), 3), ci_lb = round(exp(res$ci.lb), 3),
             ci_ub = round(exp(res$ci.ub), 3), I2 = round(res$I2, 1),
             Q = round(res$QE, 4), I2_lb = NA_real_, I2_ub = NA_real_,
             tau2 = round(res$tau2, 4), pi_lb = round(pi_lb, 4), pi_ub = round(pi_ub, 4),
             stringsAsFactors = FALSE)
}

out <- list()
a <- pool(d, "ALL"); if (!is.null(a)) out[[length(out) + 1]] <- a
for (m in sort(unique(d$modality))) {
  r <- pool(d[d$modality == m, ], m)
  if (!is.null(r)) out[[length(out) + 1]] <- r
}

cols <- c("group", "k", "hr", "ci_lb", "ci_ub", "I2",
          "Q", "I2_lb", "I2_ub", "tau2", "pi_lb", "pi_ub")
if (length(out) > 0) {
  write.csv(do.call(rbind, out)[, cols], outcsv, row.names = FALSE, na = "")
} else {
  write.csv(data.frame(group = character(), k = integer(), hr = numeric(),
                       ci_lb = numeric(), ci_ub = numeric(), I2 = numeric(),
                       Q = numeric(), I2_lb = numeric(), I2_ub = numeric(),
                       tau2 = numeric(), pi_lb = numeric(), pi_ub = numeric()),
            outcsv, row.names = FALSE)
}

if (nrow(d) >= 2) {
  res <- tryCatch(rma(yi = d$yi, vi = d$vi, method = "REML"), error = function(e) NULL)
  if (!is.null(res)) {
    png(png_path, width = 1000, height = max(380, 26 * nrow(d) + 200), res = 130)
    tryCatch(
      forest(res, transf = exp, refline = 1,
             slab = paste0(d$pmid, " (", d$modality, ")"),
             header = label, xlab = "Hazard ratio"),
      error = function(e) plot.new()
    )
    dev.off()
  }
}
