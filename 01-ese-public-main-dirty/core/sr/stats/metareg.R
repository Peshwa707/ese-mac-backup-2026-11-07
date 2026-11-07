#!/usr/bin/env Rscript
# Meta-regression with metafor::rma(yi, vi, mods = ~ moderator).
# Usage: Rscript metareg.R <input_csv> <results_csv> <moderator_label>
# input_csv columns: pmid, yi, vi, moderator

args <- commandArgs(trailingOnly = TRUE)
incsv <- args[1]; outcsv <- args[2]
moderator_label <- if (length(args) >= 3) args[3] else "moderator"

suppressMessages(library(metafor))
d <- read.csv(incsv, stringsAsFactors = FALSE)

num <- function(x) {
  if (length(x) == 0 || is.null(x)) return(NA_real_)
  as.numeric(x[1])
}

res <- rma(yi = d$yi, vi = d$vi, mods = ~ moderator, data = d, method = "REML")
coefs <- coef(res)
ses <- res$se
pvals <- res$pval

term_names <- names(coefs)
term_names <- gsub("moderator", moderator_label, term_names, fixed = TRUE)
out <- data.frame(
  term = term_names,
  estimate = round(as.numeric(coefs), 6),
  se = round(as.numeric(ses), 6),
  ci_lb = round(as.numeric(res$ci.lb), 6),
  ci_ub = round(as.numeric(res$ci.ub), 6),
  pval = round(as.numeric(pvals), 6),
  QM = round(num(res$QM), 6),
  QM_p = round(num(res$QMp), 6),
  QE = round(num(res$QE), 6),
  QE_p = round(num(res$QEp), 6),
  I2_resid = round(num(res$I2), 6),
  R2 = round(num(res$R2), 6),
  k = nrow(d),
  stringsAsFactors = FALSE
)
write.csv(out, outcsv, row.names = FALSE, na = "")
