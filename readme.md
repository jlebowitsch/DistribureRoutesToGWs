## routeswitchbysubnet for Autoscaling groups of Gateways

The purpose of the script **routeswitchbysubnet** is to allow, in AWS VPCs, the automatic reassignments of route-targets to healthy gateways, when these gateways are monitored by an ELB.
The typical use case is a Check Point vSEC Autoscaling group. By default these gateways are not configured as targets in any routes, and so they're not used for the protection of outbound traffic, except through a proxy. By using this script you'll be able to have the members configured as the default gateways for internet-bound (or other) traffic, and thus protect such traffic flow.
The way the script works is to maintain for each protected subnet a route table that points to a healthy gateway in the ASG. As gateways are added or removed, the script will detect it (in about 1 min) and will reassign route tables as necessary to the least used gateway. 



### Prerequisites and installation

- Have gateways (Check Point vSEC gateways) up and running.  
- if the gateway has more than one ENI, make sure that the gateway knows to route to the subnets behind it through ethN where N is maximal. E.g., if the gateway has eth0 and eth1, make sure that each of the gateways knows that traffic to each of the protected subnets should be forwarded through eth1
- Set up an ELB to monitor the gateways. (the gateways can be part of an Autoscaling Group or not)
- Create an SNS topic and associate it with the following:
    - cloudwatch alarm associated with the ELB that will send notification if the number of unhealthy instances is larger than 0. Add to the alarm a notification, to the same topic, if the status changes back to OK
    - a notification set on the Autoscaling group itself whenever an instance is added or removed	
- Create a lambda function that uses the script routeswitchbysubnet.py, and have it triggered by notifications to that SNS topic 
- Change the value of the following variables that you see in the begining of the script:
    - *elbname*: the name of the loadbalancer that's monitoring your gateways (e.g., 'myELB')
    - *inputsubnets*: the list of subnets that needs to route through the gateways (e.g., ['subnet-1','subnet-2'])

- By default the script uses a more advanced GW matching algorithm for each route. If you want to revert to an earlier version choose a value other than 1 to the param UseFancyDecision


### What the script does
 
1) Create a list of all the Gateways that are associated with the ELB
2) find all the route tables that are already associated with the subnets that have these routes, and create route tables for the subnets for which it doesn't find 
3) for each such route table, it finds what's the AZ that's the most common among all the subnets that use this route table
4) for each such route table, it's trying to see if there's a gateway that's better matching for it, given the AZs of the subnets using the route table, and the load on the GW

### Note to consider
currently Cloudwatch alerts must aggregate events at minimal time resultion of one minute. Thus this method of controlling routes will typically lag by one minute after a gateway first becomes "OutofService". More immdiate ways to trigger the the function RouteSwitchv2(elbname) would result in a more timely route modification

elbname='Check-Poi-ElasticL-1F7NSG2C2RFMJ'
inputsubnets=['subnet-f8b58b9d','subnet-1354e55a','subnet-b85480e2','subnet-cbad6990','subnet-f8bxxxxx']
Routetargets=['0.0.0.0/0','192.168.2.0/29']
