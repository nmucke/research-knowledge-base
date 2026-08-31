# Tag registry

## Data

### `data/direct-numerical-simulation`

Papers whose data comes from fully resolved direct numerical simulations, for
example as reference or training data for a coarser model.

## Domain

### `domain/fluid-dynamics`

Papers concerning fluid flow, CFD, Navier–Stokes equations, turbulence, LES, or
related flow systems.

### `domain/weather`

Papers concerning weather prediction, atmospheric modelling, meteorology, or
numerical weather models.

## Method

### `method/diffusion-models`

Score-based or diffusion-based generative modelling.

### `method/neural-ode`

Methods embedding a learned component inside a differentiable ODE solver, so
that training differentiates through the time integration.

### `method/stochastic-interpolants`

Methods explicitly using stochastic interpolants or closely equivalent
formulations.

## Property

### `property/structure-preserving`

Papers where the method preserves a structural property of the physical system by
construction, such as a constraint, invariant, symmetry, or conservation law.

## Task

### `task/closure-modeling`

Papers whose task is modelling unresolved or subgrid terms so that a coarse or
reduced model reproduces the behaviour of a finer reference model.

### `task/inverse-problems`

Papers whose task is recovering parameters, states, or fields from indirect,
incomplete, or noisy observations, typically requiring regularization because
the problem is ill-posed.

### `task/uncertainty-quantification`

Papers that quantify uncertainty in their predictions, for example through
posterior sampling, ensembles, or calibrated predictive intervals, rather than
producing a single point estimate.
