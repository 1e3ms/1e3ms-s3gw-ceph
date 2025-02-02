set(MAX_COMPILE_MEM 3500 CACHE INTERNAL "maximum memory used by each compiling job (in MiB)")
set(MAX_LINK_MEM 4500 CACHE INTERNAL "maximum memory used by each linking job (in MiB)")

cmake_host_system_information(RESULT _num_cores QUERY NUMBER_OF_LOGICAL_CORES)
# This will never be zero on a real system, but it can be if you're doing
# weird things like trying to cross-compile using qemu emulation.
if(_num_cores EQUAL 0)
  execute_process(
    COMMAND "nproc"
    OUTPUT_VARIABLE _num_cores
    OUTPUT_STRIP_TRAILING_WHITESPACE)
  message(WARNING "Unable to query NUMBER_OF_LOGICAL_CORES, defaulting to nproc (${_num_cores})")
endif()
cmake_host_system_information(RESULT _total_mem QUERY TOTAL_PHYSICAL_MEMORY)

math(EXPR _avg_compile_jobs "${_total_mem} / ${MAX_COMPILE_MEM}")
if(_avg_compile_jobs EQUAL 0)
  set(_avg_compile_jobs 1)
endif()
if(_num_cores LESS _avg_compile_jobs)
  set(_avg_compile_jobs ${_num_cores})
endif()
set(NINJA_MAX_COMPILE_JOBS "${_avg_compile_jobs}" CACHE STRING
  "The maximum number of concurrent compilation jobs, for Ninja build system." FORCE)
mark_as_advanced(NINJA_MAX_COMPILE_JOBS)
if(NINJA_MAX_COMPILE_JOBS)
  math(EXPR _heavy_compile_jobs "${_avg_compile_jobs} / 2")
  if(_heavy_compile_jobs EQUAL 0)
    set(_heavy_compile_jobs 1)
  endif()
  set_property(GLOBAL APPEND PROPERTY JOB_POOLS
    avg_compile_job_pool=${NINJA_MAX_COMPILE_JOBS}
    heavy_compile_job_pool=${_heavy_compile_jobs})
  set(CMAKE_JOB_POOL_COMPILE avg_compile_job_pool)
endif()

math(EXPR _avg_link_jobs "${_total_mem} / ${MAX_LINK_MEM}")
if(_avg_link_jobs EQUAL 0)
  set(_avg_link_jobs 1)
endif()
if(_num_cores LESS _avg_link_jobs)
  set(_avg_link_jobs ${_num_cores})
endif()
set(NINJA_MAX_LINK_JOBS "${_avg_link_jobs}" CACHE STRING
  "The maximum number of concurrent link jobs, for Ninja build system." FORCE)
mark_as_advanced(NINJA_MAX_LINK_JOBS)
if(NINJA_MAX_LINK_JOBS)
  math(EXPR _heavy_link_jobs "${_avg_link_jobs} / 2")
  if(_heavy_link_jobs EQUAL 0)
    set(_heavy_link_jobs 1)
  endif()
  set_property(GLOBAL APPEND PROPERTY JOB_POOLS
    avg_link_job_pool=${NINJA_MAX_LINK_JOBS}
    heavy_link_job_pool=${_heavy_link_jobs})
  set(CMAKE_JOB_POOL_LINK avg_link_job_pool)
endif()
