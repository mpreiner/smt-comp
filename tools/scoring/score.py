#!/usr/bin/env python3
#
# This script has been written to compute the scores of SMT-COMP
# It is purposefully trying to be flexible to changes in the scoring
# mechanisms. It can be used to apply scoring schemes from different
# years of the competition to different sets of data.
#
# This script requires the pandas data analysis framework
#
# @author Giles Reger, Aina Niemetz
# @date 2019

# Data processing library pandas
import numpy
import pandas

# Options parsing
from argparse import ArgumentParser

import os
import sys
import csv
import math
import time

g_args = None
g_non_competitive = {}
g_solver_names = {}

############################
# Helper functions

g_all_solved = pandas.Series(['sat','unsat'])
g_sat_solved = pandas.Series(['sat'])
g_unsat_solved = pandas.Series(['unsat'])

# Print error message and exit.
def die(msg):
    print("error: {}".format(msg))
    sys.exit(1)

def log(string):
    print("[score] {}".format(string))

# project out the main columns for printing
def view(data):
  return data[['benchmark','solver','result']]


def split_benchmark_division_family(x, family_func):
    division, benchmark = x[0], x[1]
    # Check if division is a logic string.
    # Note: This assumes that space names are not in upper case.
    if not division.isupper():
        division, benchmark = benchmark.split('/', 1)
    family = family_func(benchmark)
    return benchmark, division, family

# Determine the top-most directory as benchmark family.
# Note: 'benchmark' is not prefixed with the division name.
def get_family_top(benchmark):
    return benchmark.split('/', 1)[0]

# Determine the bottom-most directory as benchmark family.
# Note: 'benchmark' is not prefixed with the division name.
def get_family_bot(benchmark):
    return benchmark.rsplit('/', 1)[0]

# adds columns for division and family to data
# also does some tidying of benchmark column for specific years of the competition
# edit this function if you want to edit how families are added
def add_division_family_info(data, family_definition):

    # Select family extraction functions.
    # This depends on the family_definition option:
    #   - 'top' interprets the top-most directory, and
    #   - 'bot' interprets the bottom-most directory as benchmark family.
    # The rules have always specified 'top' but the scoring scripts for many
    # years actually implemented 'bot'. The scripts allow you to choose.
    fam_func = None
    if family_definition == 'top':
        fam_func = get_family_top
    elif family_definition == 'bot':
        fam_func = get_family_bot
    else:
        die('Family option not supported: {}'.format(family_definition))

    split = data['benchmark'].str.split('/', n=1)
    split = split.map(lambda x: split_benchmark_division_family(x, fam_func))
    data['benchmark'] = split.str[0]
    data['division'] = split.str[1]
    data['family'] = split.str[2]

    return data

# Drop any rows that contain benchmarks with status unknown where two otherwise
# sound solvers disagree on the result.
def remove_disagreements(data):
    global g_args

    # First find and filter out unsound solvers ,i.e., solvers that disagree
    # with the expected status.
    unsound_solvers = set(data[(data.expected != "starexec-unknown")
                               & (data.result != "starexec-unknown")
                               & (data.result != data.expected)]['solver'])

    # Consider only unknown benchmarks that were solved by sound solvers.
    solved_unknown = data[(data.expected == "starexec-unknown")
                          & (~data.solver.isin(unsound_solvers))
                          & ((data.result == 'sat') | (data.result == 'unsat'))]

    # Remove duplicate (benchmark, result) pairs to produces unique
    # result values for each benchmark.
    solved_unknown = solved_unknown.drop_duplicates(
                            subset=['benchmark', 'result'])

    # Group by benchmarks and count the number of results.
    grouped_results = solved_unknown.groupby('benchmark', as_index=False).agg(
                            {'result': 'count'})

    # If the number of results is more than one, we have disagreeing solvers, 
    # i.e., the result column contains 'sat' and 'unsat' for the corresponding
    # benchmark.
    disagreements = grouped_results[grouped_results['result'] > 1]

    exclude = set(disagreements.benchmark)

    if g_args.log:
        log('Found {} disagreements:'.format(len(exclude)))
        i = 1
        for b in exclude:
            log('[{}] {}'.format(i, b))
            i += 1

    # Exclude benchmarks on which solvers disagree.
    data = data[~(data.benchmark.isin(exclude))]
    return data

# Returns true if the solver is competitive in the given year.
# This function depends on an external file 'noncompetitive.csv' which is
# provided and maintained for the official competition data
def is_competitive(year, solver):
    global g_non_competitive
    solvers = g_non_competitive.get(year)
    return not solvers or solver not in solvers

def read_competitive():
    global g_non_competitive
    with open('noncompetitive.csv', mode='r') as f:
        reader = csv.reader(f)
        for rows in reader:
            year = rows[0]
            solver = rows[1]

            if year not in g_non_competitive:
                g_non_competitive[year] = set()
            g_non_competitive[year].add(solver)


# Use the names in the file name_lookups.csv to rename the given solver
# This is used to print nice output. If you want to change how a solver
# appears in output you should update name_lookups.csv
def solver_str(solver):
    global g_solver_names
    return g_solver_names.get(solver, solver)

def read_solver_names():
    global g_solver_names
    with open('name_lookup.csv', mode='r') as f:
      reader = csv.reader(f)
      g_solver_names = dict((r[0], r[1]) for r in reader)

# Use the names in the file name_lookups.csv to rename the names of solvers
# This is used to print nice output. If you want to change how a solver
# appears in output you should update name_lookups.csv
def rename_solvers(data):
    data.solver = data.solver.map(solver_str)
    return data

# compute family scores
# this is based on the presentation in the SMT-COMP 2017 rules document
# but this is basically the same in all rules documents
def get_family_scores(data):
    if data.empty:
        return {}

    raw_fam_scores = {} # The 'raw' score is alpha_b for b in the family.
    score_sum = 0       # The sum in the definition of alpha_b_prime.

    for family, fdata in data.groupby('family'):
        Fb = len(fdata.benchmark.unique())
        alpha_b = (1.0 + math.log(Fb)) / Fb
        raw_fam_scores[family] = alpha_b
        score_sum += Fb * alpha_b

    # Compute normalized weight alpha_prime_b for each benchmark family.
    family_scores = dict((family, alpha_b / score_sum)
                            for family, alpha_b in raw_fam_scores.items())

    return family_scores

# Selects the winners (e.g. rank 0) from the results for a division and year
# Returns these as a list (there may be more than one)
def select(results, division, year):
    return results[(results.year == year)
                   & (results.division == division)].groupby(
                        ['year', 'division']).first()['solver'].tolist()

# The same as select but turns the winners into a pretty string
def select_str(results, division, year):
  winners = select(results,division,year)
  winners_strs = sorted(map(lambda s: solver_str(s) if is_competitive(year,s) else "["+solver_str(s)+"]", winners))
  return " ".join(winners_strs)


def check_merge_solver_names(x):
    return x[0] if x[0] == x[1] else '{} {}'.format(x[1], x[0])

# Checks the winners recorded in new_results against an existing winners.csv file
# This was used to validate this script against previous results computed by
# other scripts
def check_winners(new_results, year):
    # First load the previous files from a winners file, which should be a CSV 
    winners_old = pandas.read_csv("winners.csv")

    old = winners_old[['Division', year]].set_index(['Division'])
    old.columns = ['solver_old']

    # Get all division winners from year 'year'.
    new = new_results.xs(year).groupby(level=0).first()[['solver']]
    new['solver'] = new.solver.map(solver_str)
    new['solver_comp'] = new_results[new_results.competitive == True].xs(year).groupby(level=0).first()[['solver']]
    new['solver_comp'] = new.solver_comp.map(solver_str)
    new['solver'] = new[['solver', 'solver_comp']].apply(
                                    check_merge_solver_names, axis=1)

    merged = new.merge(old, left_index=True, right_index=True, how='outer')
    diff = merged[(merged.solver.notna() | merged.solver_old.notna())
                  & (merged.solver != merged.solver_old)]

    if len(diff) > 0:
        print('Found difference in old and new results {}:'.format(year))
        print(diff[['solver_old', 'solver']])

# Turns a set of results into a LaTeX table that lists winners/best solvers
# per division as listed in the report for 2015-2018.
def to_latex_for_report(results):
     print("\begin{tabular}{"\
           "r@{\hskip 1em}>{\columncolor{white}[.25em][.5em]}"\
           "c@{\hskip 1em}>{\columncolor{white}[.5em][.5em]}"\
           "c@{\hskip 1em}>{\columncolor{white}[.5em][.5em]}"\
           "c@{\hskip 1em}>{\columncolor{white}[.5em][0.5em]}c}")
     print("\\toprule")
     print("Division & 2015 & 2016 & 2017 & 2018 \\\\")
     print("\\hline\\hline")

     divisions = results.division.unique()
     for division in divisions:
       print("\\wc {} & {} & {} & {} & {} \\\\".format(
           division,
           select_str(results, division, "2015"),
           select_str(results, division, "2016"),
           select_str(results, division, "2017"),
           select_str(results, division, "2018")))
     print("\\bottomrule")
     print("\\end{tabular}")

############################
# Scoring functions

def group_and_rank_solver(data):

    num_benchmarks = len(data.benchmark.unique())

    # Group results
    data_grouped = data.groupby(['year', 'division', 'solver']).agg({
        'correct': sum,
        'error': sum,
        'score_correct': sum,
        'score_error': sum,
        'score_cpu_time': sum,
        'score_wallclock_time': sum
        })

    # Compute percentage of solved benchmarks
    data_grouped['psolved'] = 100.0 * (data_grouped.correct / num_benchmarks)

    # MultiIndex:
    # index[0] ... year
    # index[1] ... division
    # index[2] ... solver
    data_grouped['competitive'] = \
        data_grouped.index.map(lambda x: is_competitive(x[0], x[2]))

    # Rank solvers
    data_sorted = data_grouped.sort_values(
                    by=['score_error', 'score_correct', 'score_wallclock_time',
                        'score_cpu_time'],
                    ascending=[True, False, True, True])
    data_sorted = data_sorted.sort_index(level=[0,1], sort_remaining=False)

    # Convert solver index to column
    data_sorted.reset_index(level=2, inplace=True)

    ## Rank solvers
    #data_sorted['rank'] = [i + 1 for i in range(len(data_sorted))]

    return data_sorted


# Main scoring function that allows it to capture different scoring schemes.
# division       : the division to compute the scores for
# data           : the results data of this division
# wclock_limit   : the wallclock time limit
# year           : the string identifying the year of the results
# verdicts       : a pandas.Series created with
#                  - ['sat', 'unsat'] to consider all solved instances
#                  - ['sat'] to consider only sat instances
#                  - ['unsat'] to consider only unsat instances
# use_families   : use weighted scoring scheme (as used from 2016-2018)
# skip_unknowns  : skip benchmarks with status unknown (as done prior to 2017)
def score(division,
          data,
          wclock_limit,
          verdicts,
          year,
          use_families,
          skip_unknowns):
    global g_args
    if g_args.log: log("Score for {} in {}".format(year, division))

    num_benchmarks = len(data.benchmark.unique())
    if g_args.log: log("Computing scores for {}".format(division))
    if g_args.log: log("... with {} benchmarks".format(num_benchmarks))

    family_scores = get_family_scores(data) if use_families else {}


    # Create new dataframe with relevant columns and populate new columns
    data_new = data[['division', 'benchmark', 'family', 'solver', 'cpu_time',
                     'wallclock_time', 'result', 'expected']].copy()

    data_new['year'] = year
    data_new['score_error'] = 0
    data_new['score_correct'] = 0
    data_new['score_cpu_time'] = 0
    data_new['score_wallclock_time'] = 0
    data_new['correct'] = 0     # Number of correctly solved benchmarks
    data_new['error'] = 0       # Number of wrong results
    data_new['competitive'] = False

    # Get all job pairs on which solvers were wrong
    data_new.loc[(data_new.result != 'starexec-unknown')
                 & (data_new.result != data_new.expected)
                 & (data_new.expected != 'starexec-unknown'), 'error'] = 1

    # Set alpha_prime_b for each benchmark, set to 1 if family is not in the
    # 'family_scores' dictionary (use_families == False).
    data_new['alpha_prime_b'] = \
        data_new.family.map(lambda x: family_scores.get(x, 1))

    if use_families:
        data_new['score_modifier'] = data_new.alpha_prime_b * num_benchmarks
    else:
        data_new['score_modifier'] = 1

    data_solved = data_new[(data_new.result.isin(set(verdicts)))]
    if g_args.sequential:
        data_solved = data_solved[(data_solved.cpu_time <= wclock_limit)]
    else:
        data_solved = data_solved[(data_solved.wallclock_time <= wclock_limit)]

    if skip_unknowns:
        data_solved = data_solved[(data_solved.result == data_solved.expected)]
    else:
        data_solved = data_solved[(data_solved.expected == "starexec-unknown")
                                  | (data_solved.result == data_solved.expected)]

    data_new.loc[data_solved.index, 'correct'] = 1

    # Compute scores
    data_new.score_correct = data_new.correct * data_new.score_modifier
    data_new.score_error = data_new.error * data_new.score_modifier
    data_new.score_cpu_time = data_new.cpu_time * data_new.alpha_prime_b
    data_new.score_wallclock_time = \
        data_new.wallclock_time * data_new.alpha_prime_b

    # Compute time scores only for correctly solved
    #data_new.loc[data_solved.index, 'score_cpu_time'] = \
    #    score_cpu_timedata_new.cpu_time * data_new.alpha_prime_b
    #data_new.loc[data_solved.index, 'score_wallclock_time'] = \
    #    data_new.wallclock_time * data_new.alpha_prime_b

    data_new.competitive = data_new.solver.map(lambda x: is_competitive(year, x))

    # Delete temporary columns
    return data_new.drop(columns=['alpha_prime_b', 'score_modifier'])


############################
# Processing


# Process a CSV file with results of one track.
# csv          : the input csv
# disagreements: set to True to remove disagreements
# year         : the string identifying the year of the results
# verdicts     : a pandas.Series created with
#                - ['sat', 'unsat'] to consider all solved instances
#                - ['sat'] to consider only sat instances
#                - ['unsat'] to consider only unsat instances
# use_families : use weighted scoring scheme
# skip_unknowns: skip benchmarks with status unknown
def process_csv(csv,
                disagreements,
                year,
                time_limit,
                verdicts,
                use_families,
                skip_unknowns):
    global g_args
    if g_args.log:
        log("Process {} with family: '{}', divisions: '{}', "\
            "disagreements: '{}', year: '{}', time_limit: '{}', "\
            "use_families: '{}', skip_unknowns: '{}', sequential: '{}', "\
            "verdicts: '{}'".format(
            csv,
            g_args.family,
            g_args.divisions,
            disagreements,
            year,
            time_limit,
            g_args.use_families,
            skip_unknowns,
            g_args.sequential,
            verdicts))

    # Load CSV file
    start = time.time() if g_args.show_timestamps else None
    data = pandas.read_csv(csv)
    if g_args.show_timestamps:
        log('time read_csv: {}'.format(time.time() - start))

    # Remove spaces from columns for ease (other functions rely on this)
    cols = data.columns
    cols = cols.map(lambda x: x.replace(' ', '_'))
    data.columns = cols

    start = time.time() if g_args.show_timestamps else None
    data = add_division_family_info(data, g_args.family)
    if g_args.show_timestamps:
        log('time add_division_family: {}'.format(time.time() - start))

    # -: consider all divisions
    # else list with divisions to consider
    if g_args.divisions != "-":
        divisions = g_args.divisions
        data = data[(data.division.isin(set(divisions)))]

    if disagreements:
        start = time.time() if g_args.show_timestamps else None
        data = remove_disagreements(data)
        if g_args.show_timestamps:
            log('time disagreements: {}'.format(time.time() - start))

    start = time.time() if g_args.show_timestamps else None
    # Compute the benchmark scores for each division
    dfs = []
    for division, division_data in data.groupby('division'):
        if g_args.log: log("Compute for {}".format(division))
        res = score(division,
                    division_data,
                    time_limit,
                    verdicts,
                    year,
                    use_families,
                    skip_unknowns)
        dfs.append(res)
    if g_args.show_timestamps:
        log('time score: {}'.format(time.time() - start))

    return pandas.concat(dfs, ignore_index=True)


# This function runs with specific values for certain years but keeps some
# options open to allow us to try diferent things
def gen_results_for_report_aux(verdicts, time_limit, bytotal, skip_unknowns):
    global g_args
    dataframes = []
    dataframes.append(
            process_csv(
                g_args.csv['2015'][0],
                False,
                '2015',
                min(g_args.csv['2015'][1], time_limit),
                verdicts,
                False,
                skip_unknowns))
    dataframes.append(
            process_csv(
                g_args.csv['2016'][0],
                False,
                '2016',
                min(g_args.csv['2016'][1], time_limit),
                verdicts,
                not bytotal,
                skip_unknowns))
    dataframes.append(
            process_csv(
                g_args.csv['2017'][0],
                True,
                '2017',
                min(g_args.csv['2017'][1], time_limit),
                verdicts,
                not bytotal,
                skip_unknowns))
    dataframes.append(
            process_csv(
                g_args.csv['2018'][0],
                True,
                '2018',
                min(g_args.csv['2018'][1], time_limit),
                verdicts,
                not bytotal,
                skip_unknowns))

    df = pandas.concat(dataframes, ignore_index=True)
    return group_and_rank_solver(df)


def gen_results_for_report():
    global g_args
    global g_all_solved, g_sat_solved, g_unsat_solved

    print("PARALLEL")
    start = time.time() if g_args.show_timestamps else None
    normal = gen_results_for_report_aux(g_all_solved, 2400, False, False)
    check_all_winners(normal)
    to_latex_for_report(normal)
    #vbs_winners(normal)
    #biggest_lead_ranking(normal,"a_normal")
    if g_args.show_timestamps:
        log('time parallel: {}'.format(time.time() - start))

    print("UNSAT")
    start = time.time() if g_args.show_timestamps else None
    unsat = gen_results_for_report_aux(g_unsat_solved, 2400, False, False)
    #biggest_lead_ranking(unsat,"b_unsat")
    unsat_new = project(winners(normal), winners(unsat))
    to_latex_for_report(unsat_new)
    #vbs_winners(unsat)
    if g_args.show_timestamps:
        log('time unsat: {}'.format(time.time() - start))

    print("SAT")
    start = time.time() if g_args.show_timestamps else None
    sat = gen_results_for_report_aux(g_sat_solved, 2400, False, False)
    #biggest_lead_ranking(sat,"c_sat")
    sat_new = project(winners(normal),winners(sat))
    to_latex_for_report(sat_new)
    #vbs_winners(sat)
    if g_args.show_timestamps:
        log('time sat: {}'.format(time.time() - start))

    print("24s")
    start = time.time() if g_args.show_timestamps else None
    twenty_four = gen_results_for_report_aux(g_all_solved, 24, False, False)
    #biggest_lead_ranking(twenty_four,"d_24")
    twenty_four_new = project(winners(normal),winners(twenty_four))
    to_latex_for_report(twenty_four_new)
    #vbs_winners(twenty_four)
    if g_args.show_timestamps:
        log('time 24s: {}'.format(time.time() - start))

    #print("Total Solved")
    #by_total_scored  = gen_results_for_report_aux(
    #        g_all_solved, 2400, True, False)
    #biggest_lead_ranking(by_total_scored,"e_total")
    #by_total_scored_new = project(winners(normal),winners(by_total_scored))
    #to_latex_for_report(by_total_scored_new)

    #print("Without unknowns")
    #without_unknowns  = gen_results_for_report_aux(
    #         g_all_solved, 2400, False, True)
    #without_unknowns_new = project(winners(normal),winners(without_unknowns))
    #to_latex_for_report(without_unknowns_new)

# Checks winners for a fixed number of years
# TODO: make more generic
def check_all_winners(results):
    global g_args

    print("Check differences")
    for year in g_args.year:
        print(year)
        check_winners(results, year)

def winners(data):
  top = data.copy()
  #res =  top[(data.Rank==0) & (data.competitive==True)]
  res =  top[(data.Rank==0)]
  return res

# Finds the difference between two sets of results, allows us to compare two scoring mechanisms
# This was useful when preparing the SMT-COMP journal paper and for discussing how scoring rules
# could be changed
def project(normal,other):
    normal = rename_solvers(normal)
    other = rename_solvers(other)
    different = pandas.concat(
            [normal,other],
            keys=['normal','other']).drop_duplicates(
                    keep=False,
                    subset=['year','division','solver','Rank'])
    if different.empty:
        return different
    other_different = different.loc['other']
    return other_different

# Do not consider solver variants for determining if a division is
# competitive.
# Uses solver_str(solver) to get the base version of solver.
def is_division_competitive(solvers):
    return len(set([solver_str(x) for x in solvers])) > 1


# Biggest Lead Ranking.
#
# Computes the new global ranking based on the distance between the winner of a
# division and the second solver in a division as defined in secion 7.3.1 of
# the SMT-COMP'19 rules.
#
# Note: The function prints a list of sorted tuples starting with the first
#       place (winner).
#
def biggest_lead_ranking(data):
    start = time.time() if g_args.show_timestamps else None

    data = group_and_rank_solver(data)
    data = data[data.competitive == True]
    scores = []
    # TODO: use for (year, division) groupby(level ...)
    for year, ydata in data.groupby('year'):
        for division, div_data in ydata.groupby('division'):

            # Skip non-competitive divisions
            if not is_division_competitive(div_data.solver.unique()):
                continue

            assert len(div_data) >= 2
            first = div_data.iloc[0]
            second = div_data.iloc[1]
            score = ((1 + first.correct) / (1 + second.correct))
            scores.append(
                (score, first.solver, second.solver, division))

    scores_sorted = sorted(scores, reverse=True)
    print('Biggest Lead Ranking (Score, 1st Solver, 2nd Solver, Division)')
    for s in scores_sorted:
        print(s)

    if g_args.show_timestamps:
        log('time biggest_lead_ranking: {}'.format(time.time() - start))


# Largest Contribution Ranking.
#
# Compute the correctly solved score for the virtual best solver for a given
# division if the results of 'solver' are excluded. This function corresponds
# to function vbss(D,S) as defined in section 7.3.2 of the SMT-COMP'19 rules.
#
def vbss(division_data, solver):

    # For VBS we only consider correctly solved benchmarks
    data = division_data[(division_data.solver != solver)
                         & (division_data.correct > 0)
                         & (division_data.error == 0)]

    sort_columns = ['benchmark', 'score_correct', 'wallclock_time']
    sort_asc = [True, False, True]

    # Get job pair with the highest correctly solved score, which was solved
    # the fastest.
    data_vbs = data.sort_values(
                by=sort_columns, ascending=sort_asc).groupby(
                        'benchmark', as_index=False).first()
    assert len(data_vbs) == len(data.benchmark.unique())
    return data_vbs.score_correct.sum()


# Largest Contribution Ranking.
#
# Compute the largest contribution to the virtual best solver as defined in
# section 7.3.2 of the SMT-COMP'19 rules.
#
# Note: The function prints the list of division winners sorted by the computed
#       largest contribution score.
#
def largest_contribution_ranking(data):
    start = time.time() if g_args.show_timestamps else None

    data = data[(data.competitive == True)]

    scores_top = []
    for division, div_data in data.groupby('division'):
        solvers = div_data.solver.unique()

        # Skip non-competitive divisions
        if not is_division_competitive(solvers):
            continue

        vbs_score_correct = vbss(div_data, '')

        # Compute contribution to virtual best solver for each solver in the
        # division as defined in the SMT-COMP'19 rules.
        #
        # score = 1 - vbss(D, S-s) / vbss(D, S)
        #
        # D ... division
        # S ... set of all solvers in D
        # s ... current solver
        #
        scores_diff = []
        for solver in solvers:
            vbs_solver_score_correct = vbss(div_data, solver)
            score = 1 - (vbs_solver_score_correct / vbs_score_correct)
            scores_diff.append((score, len(solvers), solver, division))

        scores_diff_sorted = sorted(scores_diff, reverse=True)
        scores_top.append(scores_diff_sorted[0])

    print('Largest Contribution Ranking (Score, Division Size, Solver, Division)')
    for s in sorted(scores_top, reverse=True):
        print(s)

    if g_args.show_timestamps:
        log('time largest_contribution_ranking: {}'.format(time.time() - start))


def parse_args():
    global g_args
    parser = ArgumentParser()
    parser.add_argument ("-c", "--csv",
                         metavar="path[,path...]",
                         help="list of input csvs with results from StarExec")
    parser.add_argument ("-y", "--year",
                         metavar="year[,year...]",
                         help="list of years matching given input csvs")
    parser.add_argument ("-t", "--time",
                         metavar="time[,time...]",
                         help="list of time limits matching given input csvs")
    parser.add_argument("-f", "--family-choice",
                        action="store",
                        dest="family",
                        default="bot",
                        help="Choose notion of benchmark family"\
                              "('top' for top-most directory, "\
                              "'bot' for bottom-most directory")
    parser.add_argument("-d", "--division-only",
                        metavar="division[,division...]",
                        action="store",
                        dest="divisions",
                        default="-",
                        help="Restrict attention to a single division")
    parser.add_argument("-s", "--sequential",
                        action="store_true",
                        dest="sequential",
                        default=False,
                        help="Compute sequential scores")
    parser.add_argument("-w", "--weighted",
                        action="store_true",
                        dest="use_families",
                        default=False,
                        help="Use weighted scoring scheme")
    parser.add_argument("-u", "--skip-unknowns",
                        action="store_true",
                        dest="skip_unknowns",
                        default=False,
                        help="Skip benchmarks with unknown status")
    parser.add_argument("--report",
                        action="store_true",
                        default=False,
                        help="Produce results for JSat 2015-2018 submission")
    parser.add_argument("--show-timestamps",
                        action="store_true",
                        default=False,
                        help="Log time for computation steps")
    parser.add_argument("-l", "--log",
                        action="store_true",
                        default=False,
                        help="Enable logging")
    g_args = parser.parse_args()

    if not g_args.csv:
        die ("Missing input csv(s).")
    if not g_args.year:
        die ("Missing input year(s).")
    if not g_args.time:
        die ("Missing input time(s).")

    g_args.csv = g_args.csv.split(',') if g_args.csv else []
    g_args.year = g_args.year.split(',') if g_args.year else []
    g_args.time = g_args.time.split(',') if g_args.time else []
    g_args.time = [int(t) for t in g_args.time]

    if len(g_args.year) != len(g_args.csv):
        die ("Number of given years and csv files does not match.")
    if len(g_args.time) != len(g_args.csv):
        die ("Number of given time limits and csv files does not match.")

    if g_args.report:
        assert '2015' in g_args.year
        assert '2016' in g_args.year
        assert '2017' in g_args.year
        assert '2018' in g_args.year

    tmp = zip (g_args.csv, g_args.time)
    g_args.csv = dict(zip(g_args.year, tmp))

    if g_args.divisions != "-":
        g_args.divisions = g_args.divisions.split(',')


def main():
    global g_args
    parse_args()
    read_competitive()
    read_solver_names()


    if g_args.report:
        for year in g_args.csv:
            csv = g_args.csv[year][0]
            if not os.path.exists(csv):
                die("Given csv does not exist: {}".format(csv))
        gen_results_for_report()
    else:
        data = []
        for year in g_args.csv:
            csv, time_limit = g_args.csv[year]
            if not os.path.exists(csv):
                die("Given csv does not exist: {}".format(csv))
            df = process_csv(csv,
                             True,
                             year,
                             time_limit,
                             g_all_solved,
                             g_args.use_families,
                             g_args.skip_unknowns)
            data.append(df)
            biggest_lead_ranking(df)
            largest_contribution_ranking(df)
        result = pandas.concat(data, ignore_index = True)


if __name__ == "__main__":
    main()

