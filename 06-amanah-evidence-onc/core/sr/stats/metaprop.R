#!/usr/bin/env Rscript
# Pool a proportion outcome with metafor (Freeman-Tukey double arcsine, REML),
# overall and by modality subgroup. Writes a results CSV + a forest-plot PNG.
# Usage: Rscript metaprop.R <input_csv> <results_csv> <forest_png> <label>
# input_csv columns: pmid, modality, events, n

args <- commandArgs(trailingOnly = TRUE)
incsv <- args[1]; outcsv <- args[2]; png_path <- args[3]
label <- if (length(args) >= 4) args[4] else "Pooled proportion"

suppressMessages(library(metafor))
d <- read.csv(incsv, stringsAsFactors = FALSE)

pool <- function(sub, group) {
  if (nrow(sub) < 2) return(NULL)
  es <- escalc(measure = "PFT", xi = sub$events, ni = sub$n)
  res <- tryCatch(rma(yi, vi, data = es, method = "REML"), error = function(e) NULL)
  if (is.null(res)) return(NULL)
  pred <- predict(res, transf = transf.ipft.hm, targs = list(ni = sub$n))
  pi_lb <- if (nrow(sub) >= 3 && !is.null(pred$pi.lb)) pred$pi.lb else NA_real_
  pi_ub <- if (nrow(sub) >= 3 && !is.null(pred$pi.ub)) pred$pi.ub else NA_real_
  data.frame(group = group, k = nrow(sub),
             prop = round(pred$pred, 4), ci_lb = round(pred$ci.lb, 4),
             ci_ub = round(pred$ci.ub, 4), I2 = round(res$I2, 1),
             tau2 = round(res$tau2, 4), Q = round(res$QE, 4),
             I2_lb = NA_real_, I2_ub = NA_real_,
             pi_lb = round(pi_lb, 4), pi_ub = round(pi_ub, 4),
             stringsAsFactors = FALSE)
}

out <- list()
a <- pool(d, "ALL"); if (!is.null(a)) out[[length(out) + 1]] <- a
for (m in sort(unique(d$modality))) {
  r <- pool(d[d$modality == m, ], m)
  if (!is.null(r)) out[[length(out) + 1]] <- r
}

cols <- c("group", "k", "prop", "ci_lb", "ci_ub", "I2", "tau2",
          "Q", "I2_lb", "I2_ub", "pi_lb", "pi_ub")
if (length(out) > 0) {
  write.csv(do.call(rbind, out)[, cols], outcsv, row.names = FALSE, na = "")
} else {
  empty <- data.frame(group = character(), k = integer(), prop = numeric(),
                      ci_lb = numeric(), ci_ub = numeric(), I2 = numeric(), tau2 = numeric(),
                      Q = numeric(), I2_lb = numeric(), I2_ub = numeric(),
                      pi_lb = numeric(), pi_ub = numeric())
  write.csv(empty, outcsv, row.names = FALSE)
}

# Forest plot (overall) — only if we have >= 2 studies and a valid fit.
if (nrow(d) >= 2) {
  es <- escalc(measure = "PFT", xi = d$events, ni = d$n)
  res <- tryCatch(rma(yi, vi, data = es, method = "REML"), error = function(e) NULL)
  if (!is.null(res)) {
    png(png_path, width = 1100, height = max(400, 26 * nrow(d) + 220), res = 130)
    tryCatch(
      forest(res, transf = transf.ipft.hm, targs = list(ni = d$n),
             slab = paste0(d$pmid, " (", d$modality, ")"),
             header = label, xlab = "Proportion", refline = NA),
      error = function(e) plot.new()
    )
    dev.off()
  }
}
