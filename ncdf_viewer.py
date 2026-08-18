import netCDF4 as nc

# Open a NetCDF file
dataset = nc.Dataset('small_scale_tests\\baseline 2009 network\\baseline_2030_cy2009.nc', 'r')

# Print the dimensions
print(dataset.dimensions)

# Print the variables
print(dataset.variables)

# Access a specific variable
#temperature = dataset.variables['temperature'][:]

# Close the dataset
dataset.close()