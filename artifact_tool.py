import pandas as pd
import numpy as np

from functools import lru_cache


class ArtifactTool():
    # TODO Write a query parser that checks if query is valid for table

    def __init__(self, path):
        self._path = path
        self._hdf = pd.HDFStore(path)
        self._str = None
        self._parse_paths()

    def _parse_paths(self):
        """ Parse the paths of each leaf in the HDF file. Pull out all causes
        and risks. Also create a string that gives the path and structure of
        the hdf that the user gets when they print the AT.
        """
        self._causes = set()
        self._risks = set()

        self._str = "HDF: " + self._path + "\n"
        self._str += "---Table Map---\n"

        for item in self._hdf.items():
            path_list = item[0].split('/')
            path_list = path_list[1:]

            # Collect risks and causes
            if path_list[0] == 'cause':
                self._causes.add(path_list[1])
            if path_list[0] == "risk_factor":
                self._risks.add(path_list[1])

            self._str += str(item[0]) + "\n"

    @property
    def risks(self):
        return self._risks

    @property
    def causes(self):
        return self._causes

    def __str__(self):
        return self._str

    def __del__(self):
        self._hdf.close()

    @lru_cache(maxsize=32)
    def population_for_year_with_age_limit(self, year: int=2016, lower: float=0, upper: float=5):
        table = self._hdf.get('/population/structure')
        table = table[table.year == year]
        table = table[table.age <= upper]
        table = table[table.age >= lower]
        return table.population.sum() / 2

    @lru_cache(maxsize=32)
    def population_for_year(self, year: int=2016):
        return self.population_for_year_with_age_limit(year, 0, 1000)

    @lru_cache(maxsize=32)
    def deaths_for_year_with_age_limit(self, year: int=2016, lower: float=0, upper: float=5):
        table = self._hdf.get('/cause/all_causes/death')
        table = table[table.year == year]
        table = table[table.age <= upper]
        table = table[table.age >= lower]
        return table.value.sum() / 1000 / 2

    @lru_cache(maxsize=32)
    def deaths_for_year(self, year: int =2016):
        return self.deaths_for_year_with_age_limit(year, 0, 1000)

    @lru_cache(maxsize=32)
    def live_births_for_year(self, year: int=2016):
        table = self._hdf.get('/covariate/live_births_by_sex/estimate')
        table = table[table.year == year]
        return table.mean_value.sum() / 2

    @lru_cache(maxsize=32)
    def crude_birth_rate_for_year(self, year: int=2016):
        live_birth_rate = self.live_births_for_year(year)
        population_size = self.population_for_year(year)
        return live_birth_rate / population_size * 1000

    @lru_cache(maxsize=32)
    def child_mortality_rate_for_year(self, year: int =2016):
        deaths_under_5 = self.deaths_for_year_with_age_limit(year, 0, 5)
        live_birth_rate = self.live_births_for_year(year)
        return deaths_under_5 / live_birth_rate * 1000

    @lru_cache(maxsize=32)
    def exposure_rates_by_year_with_age_limit(self, risk_factor: str, year: int=2016, lower: int=0, upper: int=5):
        assert risk_factor in self._risks, "risk_factor is not in the Artifact"
        table = self._hdf.get('/risk_factor/' + risk_factor + '/exposure')
        table = table[table.year == year]
        table = table[table.age <= upper]
        table = table[table.age >= lower]

        table = self._reduce_draws(table)
        table = self._add_population(table)
        print(table.columns)
        table['exposed'] = table.value_mean * table.population

        exposure_table = table.groupby(['parameter']).aggregate(np.sum)
        exposure_table['percent'] = exposure_table.exposed / exposure_table.population
        return pd.DataFrame(exposure_table.percent.tolist(), columns=['rate'], index=exposure_table.index)

    @lru_cache(maxsize=32)
    def relative_risk_by_year_with_age_limit(self, risk_factor: str, year: int=2016, lower: float=0, upper: float=5):
        assert risk_factor in self._risks, "risk_factor is not in the Artifact"
        table = self._hdf.get('/risk_factor/' + risk_factor + '/relative_risk')
        table = table[table.year == year]
        table = table[table.age <= upper]
        table = table[table.age >= lower]

        table = self._reduce_draws(table)
        table = self._add_population(table)
        table = table.sort_values(by=['cause', 'parameter'])

        weighted_risk = table.population * table.value_mean

        risks = []
        groups = table.groupby(['cause', 'parameter']).groups
        for key in groups:
            group_risks = weighted_risk.loc[groups[key]]
            risk_for_group = group_risks.sum() / table.population.loc[groups[key]].sum()
            risks.append(risk_for_group)

        results = pd.DataFrame(list(groups.keys()), columns=['causes', 'parameter'])
        results['risk'] = pd.Series([risk_factor for _ in range(len(results))])
        results['relative_risk'] = pd.Series(risks)
        return results

    def _reduce_draws(self, table: pd.DataFrame, val_col: str="value"):
        """Creates a DataFrame with mean and CI values obtained across draws.

        Parameters
        ----------
        table:
            A pandas DataFrame that contains a "draw" column
        col_name:
            The name of the column inside table that the mean and CI values should
            be computed from. The column should contain numeric values.

        Returns
        -------
        A table that summarizes key statistical values for a specific column
        """

        assert "draw" in table.columns, "Table does not have a column named draw"

        drawless_table = table.query('draw == 0')

        # create identifiers for each row, independent of draws
        columns = drawless_table.columns.tolist()
        columns = [c for c in columns if c not in ['draw', val_col]]

        # turn the identifiers into strings
        identifiers = []
        for index, row in drawless_table.sort_values(by=columns).iterrows():
            row_list = row[columns].tolist()
            ID = '-'.join([str(row) for row in row_list])
            identifiers.append(ID)

        # create a table that has draws as rows and identifiers as columns
        table = table.sort_values(by=['draw'] + columns)
        values = table[val_col].values
        values = values.reshape(len(table) // len(identifiers), len(identifiers))
        value_df = pd.DataFrame(values, columns=identifiers)

        # remove the values column from our drawless table and add columns for the
        # stats we want
        result_df = drawless_table[columns].sort_values(by=columns)
        result_df[val_col + "_mean"] = [value_df[col].values.mean() for col in identifiers]
        result_df['lower 2.5'] = [np.percentile(value_df[col].values, 2.5) for col in identifiers]
        result_df['upper 97.5'] = [np.percentile(value_df[col].values, 97.5) for col in identifiers]
        result_df = result_df.reset_index(drop=True)

        return result_df

    def _add_population(self, table: pd.DataFrame):
        """ Maps data on age, sex and year to populations.

        Parameters
        ----------
        table:
            A pandas DataFrame that contains "age", "sex" and "year" columns

        Returns
        -------
        table with a column named population appended to it.
        """
        assert all([col_name in table.columns for col_name in ["age", "year", "sex"]]), "table does not have all the required columns"
        pop_table = self._hdf.get('/population/structure')
        # Set each column to a str so we can hash them
        table_age = table.age.astype(str)
        table_year = table.year.astype(str)
        table_sex = table.sex.astype(str)
        pop_age = pop_table.age.astype(str)
        pop_year = pop_table.year.astype(str)
        pop_sex = pop_table.sex.astype(str)

        table_hash = (table_age + table_year + table_sex)
        pop_table['hash'] = (pop_age + pop_year + pop_sex)

        @lru_cache(maxsize=256)
        def hash_to_pop(hash_key):
            return float(pop_table[pop_table.hash == hash_key].population.values)
        table['population'] = table_hash.map(hash_to_pop)

        pop_table = pop_table.drop(columns=['hash'])
        return table
