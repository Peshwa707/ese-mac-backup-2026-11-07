#!/usr/bin/env Rscript
# Sensitivity + small-study/publication-bias diagnostics for one pooled effect.
# Leave-one-out (k>=3); Egger regression + Begg rank test + trim-and-fill (k>=10,
# per Cochrane/Sterne 2011 — underpowered below that, so honestly NOT run). Funnel
# PNG drawn for k>=3 with a caution flag when k<10.
# Usage: Rscript diagnostics.R <in_csv> <loo_csv> <pubbias_csv> <funnel_png> <label>
# in_csv columns: pmid, yi (log-effect), vi (variance)

args <- commandArgs(trailingOnly = TRUE)
incsv <- args[1]; loocsv <- args[2]; pbcsv <- args[3]; funnel_png <- args[4]
label <- if (length(args) >= 5) args[5] else "Pooled effect"

suppressMessages(library(metafor))
d <- read.csv(incsv, stringsAsFactors = FALSE)
k <- nrow(d)

emptyc <- function() write.csv(data.frame(), loocsv, row.names = FALSE)
res <- tryCatch(rma(yi = d$yi, vi = d$vi, method = "REML"), error = function(e) NULL)

# --- Leave-one-out (k>=3) ---
if (!is.null(res) && k >= 3) {
  lo <- tryCatch(leave1out(res), error = function(e) NULL)
  if (!is.null(lo)) {
    loo <- data.frame(omitted = d$pmid,
                      hr = round(exp(lo$estimate), 3),
                      ci_lb = round(exp(lo$estimate - 1.96 * lo$se), 3),
                      ci_ub = round(exp(lo$estimate + 1.96 * lo$se), 3),
                      I2 = round(lo$I2, 1), stringsAsFactors = FALSE)
    write.csv(loo, loocsv, row.names = FALSE)
  } else emptyc()
} else emptyc()

# --- Publication bias (k>=10 gate) ---
egger_p <- NA; begg_p <- NA; tf_hr <- NA; tf_lb <- NA; tf_ub <- NA; k0 <- NA
note <- ""
if (is.null(res)) {
  note <- "pooling failed"
} else if (k < 10) {
  note <- sprintf("not performed: k=%d (<10; small-study tests underpowered, Cochrane/Sterne 2011)", k)
} else {
  eg <- tryCatch(regtest(res, model = "lm"), error = function(e) NULL)
  if (!is.null(eg)) egger_p <- round(eg$pval, 4)
  bg <- tryCatch(ranktest(res), error = function(e) NULL)
  if (!is.null(bg)) begg_p <- round(bg$pval, 4)
  tf <- tryCatch(trimfill(res), error = function(e) NULL)
  if (!is.null(tf)) {
    tf_hr <- round(exp(tf$b[1]), 3); tf_lb <- round(exp(tf$ci.lb), 3)
    tf_ub <- round(exp(tf$ci.ub), 3); k0 <- tf$k0
  }
  note <- "performed"
}
write.csv(data.frame(k = k, egger_p = egger_p, begg_p = begg_p,
                     trimfill_hr = tf_hr, trimfill_lb = tf_lb, trimfill_ub = tf_ub,
                     n_imputed = k0, note = note, stringsAsFactors = FALSE),
          pbcsv, row.names = FALSE)

# --- Funnel (k>=3) ---
if (!is.null(res) && k >= 3) {
  png(funnel_png, width = 760, height = 620, res = 130)
  tryCatch({
    funnel(res, atransf = exp, xlab = label,
           main = if (k < 10) paste0(label, "  (k=", k, "; interpret with caution)") else label)
  }, error = function(e) plot.new())
  dev.off()
}
